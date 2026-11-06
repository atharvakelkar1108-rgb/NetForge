"""Hop-by-hop packet forwarding simulation.

forward() models one IP packet: host chooses on-link vs default gateway, ARP
resolves the next hop on the Layer-2 segment (through switches, VLAN-aware),
each router decrements TTL, checks its ACL and does a longest-prefix-match
lookup. Ping needs the echo reply to find its way back too, so a missing
return route shows up as a timeout - exactly like a real network.
"""
from __future__ import annotations

import copy
import ipaddress

from .model import Net
from .routing import compute_tables, lookup


def acl_match(spec, ip):
    if spec in (None, "", "any"):
        return True
    try:
        return ip in ipaddress.ip_network(spec, strict=False)
    except ValueError:
        return False


def acl_check(node, src, dst):
    """First matching rule wins; no match = permit. Returns (permitted, rule_no)."""
    for k, rule in enumerate(node.get("acl", []), 1):
        if acl_match(rule.get("src"), src) and acl_match(rule.get("dst"), dst):
            return rule.get("action") != "deny", k
    return True, None


def _fail(res, code, msg, at):
    res.update(ok=False, code=code, msg=msg, at=at)
    return res


def _host_egress(net, nid, dst):
    """Return ((ifname, next_hop_ip), None) or (None, (code, message))."""
    name, node = net.name(nid), net.nodes[nid]
    ups = net.up_ifaces(nid)
    if not ups:
        return None, ("no_link", f"{name} has no active interface (no cable, or the link is down)")
    for ifn, ip in ups:
        if dst in ip.network:
            return (ifn, dst), None
    gw = node.get("gateway")
    if not gw:
        return None, ("no_gateway", f"{name} has no default gateway, and {dst} is outside its subnet")
    try:
        gw = ipaddress.IPv4Address(gw)
    except ValueError:
        return None, ("no_gateway", f"{name}: default gateway '{gw}' is not a valid IP")
    for ifn, ip in ups:
        if gw in ip.network:
            return (ifn, gw), None
    return None, ("no_gateway", f"{name}: gateway {gw} is not on any directly connected subnet")


def forward(net, tables, src_ip, dst_ip, ttl=64):
    res = {"ok": False, "code": None, "msg": "", "at": None, "path": [], "hops": [], "events": [],
           "time": 0.0, "cost": 0.0, "ttl_left": ttl}
    ev = res["events"]
    try:
        s_ip = ipaddress.IPv4Address(str(src_ip).strip())
        d_ip = ipaddress.IPv4Address(str(dst_ip).strip())
    except ValueError:
        return _fail(res, "bad_ip", "Invalid IP address.", None)
    src = net.owner_of_ip(s_ip)
    if not src:
        return _fail(res, "unknown_src", f"No device owns source address {s_ip}.", None)
    if not net.owner_of_ip(d_ip):
        return _fail(res, "unknown_dst", f"No device owns destination address {d_ip}.", None)

    start = cur = src[0]
    path, t, arp = [cur], 0.0, set()
    seen = [cur] if net.nodes[cur]["type"] == "router" else []

    for _ in range(64):
        node, name = net.nodes[cur], net.name(cur)
        if net.owns(cur, d_ip):
            res.update(ok=True, path=path, time=t, ttl_left=ttl)
            ev.append(("ok", f"{name} received the packet for {d_ip} (TTL left {ttl})"))
            return res
        if node["type"] == "host":
            if cur != start:
                return _fail(res, "host_no_forward", f"{name} received a packet that is not for it - hosts do not forward", cur) | {"path": path}
            out, err = _host_egress(net, cur, d_ip)
            if err:
                return _fail(res, err[0], err[1], cur) | {"path": path}
            ev.append(("muted", f"{name}: {d_ip} is {'on-link' if out[1] == d_ip else 'off-link, sending to gateway ' + str(out[1])}"))
        elif node["type"] == "router":
            if cur != start:
                if cur in seen:
                    cyc = " -> ".join(net.name(x) for x in seen[seen.index(cur):] + [cur])
                    return _fail(res, "loop", f"Routing loop detected: {cyc}", cur) | {"path": path}
                seen.append(cur)
                ttl -= 1
                if ttl <= 0:
                    return _fail(res, "ttl", f"TTL expired in transit at {name}", cur) | {"path": path}
                ok, rule = acl_check(node, s_ip, d_ip)
                if not ok:
                    ev.append(("err", f"{name}: ACL rule #{rule} DENIES {s_ip} -> {d_ip}"))
                    return _fail(res, "acl", f"Administratively prohibited: ACL rule #{rule} on {name} blocks {s_ip} -> {d_ip}", cur) | {"path": path}
            route = lookup(tables.get(cur, []), d_ip)
            if not route:
                return _fail(res, "no_route", f"{name} has no route to {d_ip} (destination network unreachable)", cur) | {"path": path}
            nh = d_ip if route.kind == "C" else ipaddress.IPv4Address(route.next_hop)
            out = (route.out_if, nh)
            how = "directly connected" if route.kind == "C" else f"via {route.via or route.next_hop}"
            ev.append(("muted", f"{name}: route {route.net} [{route.kind}/{route.metric}] {how} out {route.out_if}"))
        else:
            return _fail(res, "bad_node", f"{name} cannot originate packets", cur)

        ifn, tgt = out
        hit = None
        for (pn, pif), (seg, c, d) in net.l2_reach(cur, ifn).items():
            pip = net.ipif(pn, pif)
            if pip and pip.ip == tgt:
                hit = (pn, pif, seg, c, d, pip)
                break
        if not hit:
            return _fail(res, "arp", f"{name} cannot reach {tgt} on {ifn}: ARP gets no answer (wrong subnet/VLAN, link down, or no such device)", cur) | {"path": path}
        pn, pif, seg, c, d, pip = hit
        if (cur, str(tgt)) not in arp:
            arp.add((cur, str(tgt)))
            ev.append(("muted", f"ARP: {name} asks who-has {tgt}? -> {net.name(pn)} replies"))
        path.extend(seg[1:])
        res["cost"] += c
        t += d + (0.5 if net.nodes[pn]["type"] == "router" else 0)
        res["hops"].append({"node": pn, "name": net.name(pn), "ip": str(pip.ip), "t": round(t, 2)})
        cur = pn
    return _fail(res, "ttl", "Too many hops", cur) | {"path": path}


def _pack(ok, log, path, rpath, failed_at, code):
    return {"success": ok, "log": log, "path": path, "return_path": rpath, "failed_at": failed_at, "code": code}


def ping(net, tables, src_ip, dst_ip, count=4):
    log = []
    L = lambda c, t: log.append({"c": c, "t": t})
    f = forward(net, tables, src_ip, dst_ip)
    src = net.owner_of_ip(src_ip)
    if not src:
        L("err", f["msg"])
        return _pack(False, log, [], [], None, f["code"])
    L("muted", f"PING {dst_ip} from {net.name(src[0])} ({src_ip}): 56 data bytes")
    for c, t in f["events"]:
        L(c, t)
    if not f["ok"]:
        L("err", f"Destination unreachable - {f['msg']}")
        L("err", f"--- {count} packets transmitted, 0 received, 100% packet loss")
        return _pack(False, log, f["path"], [], f["at"], f["code"])
    r = forward(net, tables, dst_ip, src_ip)
    if not r["ok"]:
        L("warn", f"The request arrived, but the REPLY could not get back: {r['msg']}")
        L("err", "Request timed out.")
        L("err", f"--- {count} packets transmitted, 0 received, 100% packet loss")
        return _pack(False, log, f["path"], r["path"], r["at"], "reply_" + str(r["code"]))
    rtt = f["time"] + r["time"]
    for k in range(count):
        L("ok", f"Reply from {dst_ip}: bytes=56 ttl={r['ttl_left']} time={rtt + [0, 0.4, 0.1, 0.7][k % 4]:.1f} ms")
    L("ok", f"--- {count} packets transmitted, {count} received, 0% packet loss, avg rtt {rtt + 0.3:.1f} ms")
    return _pack(True, log, f["path"], r["path"], None, None)


def traceroute(net, tables, src_ip, dst_ip):
    log = []
    L = lambda c, t: log.append({"c": c, "t": t})
    f = forward(net, tables, src_ip, dst_ip)
    src = net.owner_of_ip(src_ip)
    if not src:
        L("err", f["msg"])
        return _pack(False, log, [], [], None, f["code"])
    L("muted", f"traceroute to {dst_ip} from {net.name(src[0])}, 30 hops max")
    for k, h in enumerate(f["hops"], 1):
        L("ok", f"{k:>2}  {h['name']} ({h['ip']})  {h['t'] * 2:.1f} ms")
    if not f["ok"]:
        L("err", f"{len(f['hops']) + 1:>2}  * * *   {f['msg']}")
    return _pack(f["ok"], log, f["path"], [], None if f["ok"] else f["at"], f["code"])


def reachable(net, tables, a_ip, b_ip):
    return forward(net, tables, a_ip, b_ip)["ok"] and forward(net, tables, b_ip, a_ip)["ok"]


def matrix(net, tables):
    hosts = []
    for n in net.topo["nodes"]:
        if n["type"] == "host":
            ip = next((i["ip"] for i in n["interfaces"] if net.ipif(n["id"], i["name"])), None)
            if ip:
                hosts.append((n["hostname"], ip))
    hosts = hosts[:12]
    rows = [[None if a == b else reachable(net, tables, hosts[a][1], hosts[b][1])
             for b in range(len(hosts))] for a in range(len(hosts))]
    return {"labels": [h[0] for h in hosts], "ips": [h[1] for h in hosts], "matrix": rows}


def compare(topo, src_ip, dst_ip):
    """Run the same packet under RIP and under OSPF so the chosen paths can be compared."""
    out = []
    for proto in ("rip", "ospf"):
        t = copy.deepcopy(topo)
        t["settings"]["protocol"] = proto
        net = Net(t)
        f = forward(net, compute_tables(net), src_ip, dst_ip)
        names = []
        for nid in f["path"]:
            if not names or names[-1] != net.name(nid):
                names.append(net.name(nid))
        out.append({"protocol": proto, "ok": f["ok"], "msg": f["msg"], "names": names, "nodes": f["path"],
                    "routers": sum(1 for h in f["hops"] if net.nodes[h["node"]]["type"] == "router"),
                    "cost": f["cost"], "time": round(f["time"], 1)})
    return out

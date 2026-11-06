"""Configuration validation. Every rule appends an issue with:
severity (error|warning), rule id, message, a suggested fix, and the node/link
ids to highlight on the canvas. Health score = 100 - 15*errors - 5*warnings.
"""
from __future__ import annotations

import ipaddress
from collections import defaultdict

from .model import Net
from .routing import compute_tables, find_loops


class _UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def validate(topo):
    net = Net(topo)
    nodes = topo["nodes"]
    issues = []

    def add(sev, rule, msg, fix="", ns=(), ls=()):
        issues.append({"severity": sev, "rule": rule, "message": msg, "fix": fix,
                       "nodes": list(ns), "links": list(ls)})

    # --- links --------------------------------------------------------
    for l in net.dangling:
        add("error", "dangling_link", f"Link {l['id']} points at a device that no longer exists.",
            "Click the link and delete it.", ls=[l["id"]])
    for l in net.links:
        if l in net.dangling:
            continue
        for nid, ifn in ((l["nodeA"], l["ifaceA"]), (l["nodeB"], l["ifaceB"])):
            if not net.iface(nid, ifn):
                add("error", "bad_interface", f"{net.name(nid)} has no interface '{ifn}' used by link {l['id']}.",
                    "Delete and redraw the link.", [nid], [l["id"]])
        if not l["up"]:
            add("warning", "link_down", f"Link {net.name(l['nodeA'])} <-> {net.name(l['nodeB'])} is DOWN.",
                "Select the link and bring it back up (unless you are testing failover).", [l["nodeA"], l["nodeB"]], [l["id"]])

    # --- interfaces / addressing -------------------------------------
    ips = defaultdict(list)
    for n in nodes:
        if n["type"] == "switch":
            continue
        for i in n["interfaces"]:
            used = net.link_at(n["id"], i["name"]) is not None
            if i["ip"] and not net.ipif(n["id"], i["name"]):
                add("error", "invalid_ip", f"{n['hostname']}:{i['name']} has an invalid IP/mask ({i['ip']}/{i['mask']}).",
                    "Use a dotted IPv4 address and a mask between 0 and 32.", [n["id"]])
            elif not i["ip"] and used:
                add("error", "incomplete_interface", f"{n['hostname']}:{i['name']} is connected but has no IP address.",
                    "Give the interface an IP, or leave unused interfaces unconnected.", [n["id"]])
            elif i["ip"]:
                ips[i["ip"]].append((n, i))
    for ip, owners in ips.items():
        if len(owners) > 1:
            who = ", ".join(f"{n['hostname']}:{i['name']}" for n, i in owners)
            add("error", "duplicate_ip", f"IP {ip} is configured on more than one interface ({who}).",
                "Every interface needs a unique IP address.", [n["id"] for n, _ in owners])
    hn = defaultdict(list)
    for n in nodes:
        hn[n["hostname"]].append(n["id"])
    for h, ids in hn.items():
        if len(ids) > 1:
            add("warning", "duplicate_hostname", f"Hostname '{h}' is used by {len(ids)} devices.", "Rename one of them.", ids)

    # --- Layer-2 segment analysis (subnet / VLAN / duplicate subnets) --
    uf, pair_seen, net_members = _UF(), set(), defaultdict(set)
    for n in nodes:
        if n["type"] == "switch":
            continue
        for ifn, ip in net.up_ifaces(n["id"]):
            me = (n["id"], ifn)
            uf.find(me)
            net_members[ip.network].add(me)
            strict = net.l2_reach(n["id"], ifn)
            for peer in strict:
                uf.union(me, peer)
                pip = net.ipif(*peer)
                key = frozenset([me, peer])
                if pip and pip.network != ip.network and key not in pair_seen:
                    pair_seen.add(key)
                    add("error", "subnet_mismatch",
                        f"{n['hostname']}:{ifn} ({ip.network}) and {net.name(peer[0])}:{peer[1]} ({pip.network}) "
                        f"share a segment but are in different subnets.",
                        "Put both on the same network/mask, or separate them with a router.", [n["id"], peer[0]])
            for peer in net.l2_reach(n["id"], ifn, ignore_vlan=True):
                pip = net.ipif(*peer)
                key = frozenset([me, peer, "vlan"])
                if peer not in strict and pip and pip.network == ip.network and key not in pair_seen:
                    pair_seen.add(key)
                    add("warning", "vlan_mismatch",
                        f"{n['hostname']} (VLAN {net.vlan(n['id'], ifn)}) and {net.name(peer[0])} (VLAN {net.vlan(*peer)}) "
                        f"are on the same subnet but in different VLANs, so they cannot talk.",
                        "Give both interfaces the same VLAN id.", [n["id"], peer[0]])
    for netw, members in net_members.items():
        roots = {uf.find(m) for m in members}
        if len(roots) > 1:
            add("warning", "duplicate_subnet", f"Subnet {netw} is used on {len(roots)} separate segments - they cannot reach each other.",
                "Use a different subnet per segment.", sorted({m[0] for m in members}))
    nets = list(net_members)
    for a in range(len(nets)):
        for b in range(a + 1, len(nets)):
            if nets[a].overlaps(nets[b]):
                ids = sorted({m[0] for m in net_members[nets[a]] | net_members[nets[b]]})
                add("warning", "overlapping_subnets", f"Subnets {nets[a]} and {nets[b]} overlap.",
                    "Re-plan the addressing so subnets do not overlap.", ids)
    for n in nodes:
        ups = net.up_ifaces(n["id"])
        for a in range(len(ups)):
            for b in range(a + 1, len(ups)):
                if ups[a][1].network.overlaps(ups[b][1].network):
                    add("error", "overlapping_interfaces", f"{n['hostname']}:{ups[a][0]} and {ups[b][0]} are in overlapping subnets.",
                        "A device cannot have two interfaces in the same network.", [n["id"]])

    # --- host gateways -----------------------------------------------
    has_router = any(n["type"] == "router" for n in nodes)
    for n in nodes:
        if n["type"] != "host":
            continue
        ups = net.up_ifaces(n["id"])
        if not ups:
            continue
        gw = n.get("gateway")
        if not gw:
            if has_router:
                add("warning", "missing_gateway", f"{n['hostname']} has no default gateway.",
                    "Set it to the router interface on this host's subnet.", [n["id"]])
            continue
        try:
            gwa = ipaddress.IPv4Address(gw)
        except ValueError:
            add("error", "invalid_gateway", f"{n['hostname']}: gateway '{gw}' is not a valid IP.", "", [n["id"]])
            continue
        home = next(((ifn, ip) for ifn, ip in ups if gwa in ip.network), None)
        if not home:
            add("error", "gateway_not_in_subnet", f"{n['hostname']}'s gateway {gw} is not inside any of its subnets.",
                "The gateway must be an address in the host's own subnet.", [n["id"]])
            continue
        owner = next((p for p in net.l2_reach(n["id"], home[0])
                      if (net.ipif(*p) and net.ipif(*p).ip == gwa)), None)
        if not owner:
            add("error", "gateway_unreachable", f"{n['hostname']}'s gateway {gw} does not exist on its network segment.",
                "Set the gateway to an IP that a router interface really has on this subnet.", [n["id"]])
        elif net.nodes[owner[0]]["type"] != "router":
            add("warning", "gateway_not_router", f"{n['hostname']}'s gateway {gw} belongs to {net.name(owner[0])}, which is not a router.",
                "", [n["id"], owner[0]])

    # --- routers: static routes & ACL syntax --------------------------
    for n in nodes:
        if n["type"] != "router":
            continue
        for r in n["static_routes"]:
            try:
                ipaddress.ip_network(r["dest"], strict=False)
                nh = ipaddress.ip_address(r["next_hop"])
            except ValueError:
                add("error", "invalid_static_route", f"{n['hostname']}: static route '{r['dest']} via {r['next_hop']}' is malformed.",
                    "Use CIDR for the destination (10.0.0.0/24) and an IP for the next hop.", [n["id"]])
                continue
            if not any(nh in ip.network for _i, ip in net.up_ifaces(n["id"])):
                add("warning", "static_nexthop_unreachable", f"{n['hostname']}: next hop {nh} is not on a directly connected subnet, so the route is ignored.",
                    "", [n["id"]])
        for k, rule in enumerate(n["acl"], 1):
            for fld in ("src", "dst"):
                spec = rule.get(fld)
                if spec in (None, "", "any"):
                    continue
                try:
                    ipaddress.ip_network(spec, strict=False)
                except ValueError:
                    add("error", "invalid_acl", f"{n['hostname']} ACL rule #{k}: '{spec}' is not a valid network.",
                        "Use 'any', a host (10.0.0.5) or CIDR (10.0.0.0/24).", [n["id"]])

    # --- topology shape ----------------------------------------------
    linked = {x for l in net.links for x in (l["nodeA"], l["nodeB"])}
    for n in nodes:
        if n["id"] not in linked and len(nodes) > 1:
            add("warning", "isolated_node", f"{n['hostname']} is not connected to anything.", "Draw a link to it.", [n["id"]])
    sw = _UF()
    for l in net.links:
        if l in net.dangling or not l["up"]:
            continue
        a, b = net.nodes[l["nodeA"]], net.nodes[l["nodeB"]]
        if a["type"] == "switch" and b["type"] == "switch":
            if sw.find(a["id"]) == sw.find(b["id"]):
                add("warning", "l2_loop", f"Switches {a['hostname']} and {b['hostname']} form a Layer-2 loop (broadcast-storm risk).",
                    "Real switches use Spanning Tree (STP) to block one of the links.", [a["id"], b["id"]], [l["id"]])
            else:
                sw.union(a["id"], b["id"])
    comp = _UF()
    for l in net.links:
        if l not in net.dangling and l["up"]:
            comp.union(l["nodeA"], l["nodeB"])
    groups = {comp.find(x) for x in linked if x in net.nodes}
    if len(groups) > 1:
        add("warning", "partitioned_topology", f"The network is split into {len(groups)} disconnected parts.",
            "Connect the parts (or check for links that are down).")

    # --- routing loops ------------------------------------------------
    for lp in find_loops(net, compute_tables(net)):
        add("error", "routing_loop", f"Routing loop towards {lp['dest']}: {' -> '.join(lp['names'])} -> {lp['names'][0]}.",
            "Check static routes - two routers point at each other for the same network.", lp["routers"])

    errors = sum(i["severity"] == "error" for i in issues)
    warns = sum(i["severity"] == "warning" for i in issues)
    return {"ok": errors == 0, "issues": issues, "errors": errors, "warnings": warns,
            "score": max(0, 100 - 15 * errors - 5 * warns), "issue_count": len(issues)}

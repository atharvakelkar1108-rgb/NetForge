"""Turn the topology into Cisco-IOS-style configuration text, one block per device."""
from __future__ import annotations

import ipaddress

from .model import Net
from .routing import compute_tables


def _acl_spec(spec):
    if spec in (None, "", "any"):
        return "any"
    try:
        n = ipaddress.ip_network(spec, strict=False)
    except ValueError:
        return "any"
    return f"host {n.network_address}" if n.prefixlen == 32 else f"{n.network_address} {n.hostmask}"


def generate(topo):
    net = Net(topo)
    tables = compute_tables(net)
    proto = net.protocol
    out = {}
    for n in topo["nodes"]:
        nid, L = n["id"], [f"hostname {n['hostname']}", "!"]
        if n["type"] == "switch":
            for (l, myif, oid, _oi) in net.adj[nid]:
                peer = net.nodes[oid]
                L.append(f"interface {myif}")
                L.append(f" description to {peer['hostname']}")
                if peer["type"] == "switch":
                    L.append(" switchport mode trunk")
                else:
                    pif = l["ifaceB"] if l["nodeA"] == nid else l["ifaceA"]
                    L += [" switchport mode access", f" switchport access vlan {net.vlan(oid, pif)}"]
                L += [" no shutdown" if l["up"] else " shutdown", "!"]
            out[n["hostname"]] = "\n".join(L)
            continue
        has_acl = n["type"] == "router" and n["acl"]
        for i in n["interfaces"]:
            ip, link = net.ipif(nid, i["name"]), net.link_at(nid, i["name"])
            if not ip and not link:
                continue
            L.append(f"interface {i['name']}")
            if link:
                peer = link["nodeB"] if link["nodeA"] == nid else link["nodeA"]
                L.append(f" description to {net.name(peer)}")
            if ip:
                L.append(f" ip address {ip.ip} {ip.netmask}")
            if has_acl and ip:
                L.append(" ip access-group 101 in")
            L += [" no shutdown" if link and link["up"] else " shutdown", "!"]
        if n["type"] == "host":
            if n["gateway"]:
                L.append(f"ip default-gateway {n['gateway']}")
        else:
            for r in n["static_routes"]:
                try:
                    d = ipaddress.ip_network(r["dest"], strict=False)
                    L.append(f"ip route {d.network_address} {d.netmask} {r['next_hop']}")
                except ValueError:
                    pass
            conn = [r.net for r in tables.get(nid, []) if r.kind == "C"]
            if proto == "ospf":
                L.append("router ospf 1")
                L += [f" network {c.network_address} {c.hostmask} area 0" for c in conn]
            else:
                L += ["router rip", " version 2", " no auto-summary"]
                L += [f" network {c.network_address}" for c in conn]
            if has_acl:
                L.append("!")
                for rule in n["acl"]:
                    L.append(f"access-list 101 {'deny' if rule.get('action') == 'deny' else 'permit'} ip "
                             f"{_acl_spec(rule.get('src'))} {_acl_spec(rule.get('dst'))}")
                L.append("access-list 101 permit ip any any")
        out[n["hostname"]] = "\n".join(L)
    combined = "\n\n".join(f"! ===== {h} =====\n{t}" for h, t in out.items())
    return {"configs": out, "combined": combined}

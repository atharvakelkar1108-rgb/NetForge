"""Topology model + Layer-2 / Layer-3 helpers shared by every engine module.

A topology is plain JSON:
  nodes: [{id, type(router|switch|host), hostname, x, y,
           interfaces:[{name, ip, mask, vlan}],
           gateway (hosts), static_routes:[{dest, next_hop}] (routers),
           acl:[{action, src, dst}] (routers)}]
  links: [{id, nodeA, ifaceA, nodeB, ifaceB, cost, delay, up}]
  settings: {protocol: "ospf" | "rip"}

Every API call carries the whole topology, so the backend is stateless and
always works on exactly what is on the user's screen.
"""
from __future__ import annotations

import copy
import ipaddress
from collections import deque


def split_cidr(ip, mask=""):
    """'10.0.0.2/24' typed into the IP box -> ('10.0.0.2', '24')."""
    ip = (ip or "").strip()
    mask = str(mask if mask is not None else "").strip()
    if "/" in ip:
        ip, _, m = ip.partition("/")
        ip = ip.strip()
        if m.strip():
            mask = m.strip()
    return ip, mask


def _num(v, default):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if f <= 0:
        return default
    return int(f) if f == int(f) else f


def normalise(topo):
    """Return a cleaned deep copy with every optional field filled in."""
    t = copy.deepcopy(topo or {})
    t.setdefault("nodes", [])
    t.setdefault("links", [])
    proto = (t.get("settings") or {}).get("protocol", "ospf")
    t["settings"] = {"protocol": proto if proto in ("ospf", "rip") else "ospf"}
    for n in t["nodes"]:
        n.setdefault("interfaces", [])
        n.setdefault("static_routes", [])
        n.setdefault("acl", [])
        n.setdefault("hostname", n.get("id", "?"))
        n["gateway"] = split_cidr(n.get("gateway"), "")[0]
        for i in n["interfaces"]:
            i["ip"], i["mask"] = split_cidr(i.get("ip"), i.get("mask", "24"))
            i["mask"] = i["mask"] or "24"
            i["vlan"] = str(i.get("vlan") or "").strip()
        for r in n["static_routes"]:
            r["next_hop"] = split_cidr(r.get("next_hop"), "")[0]
            r["dest"] = (r.get("dest") or "").strip()
    for k, l in enumerate(t["links"]):
        l.setdefault("id", f"l{k + 1}")
        l["cost"] = _num(l.get("cost"), 1)
        l["delay"] = _num(l.get("delay"), 2)
        l["up"] = l.get("up", True) is not False
    return t


class Net:
    """Read-only view over a normalised topology with lookup helpers."""

    def __init__(self, topo):
        self.topo = topo
        self.nodes = {n["id"]: n for n in topo["nodes"]}
        self.links = topo["links"]
        self.protocol = topo["settings"]["protocol"]
        self.adj = {nid: [] for nid in self.nodes}   # nid -> [(link, myif, other, otherif)]
        self.dangling = []
        for l in self.links:
            if l["nodeA"] in self.nodes and l["nodeB"] in self.nodes:
                self.adj[l["nodeA"]].append((l, l["ifaceA"], l["nodeB"], l["ifaceB"]))
                self.adj[l["nodeB"]].append((l, l["ifaceB"], l["nodeA"], l["ifaceA"]))
            else:
                self.dangling.append(l)

    # ---- basic lookups -------------------------------------------------
    def name(self, nid):
        return self.nodes[nid]["hostname"]

    def routers(self):
        return [n["id"] for n in self.topo["nodes"] if n["type"] == "router"]

    def iface(self, nid, ifname):
        return next((i for i in self.nodes[nid]["interfaces"] if i["name"] == ifname), None)

    def ipif(self, nid, ifname):
        i = self.iface(nid, ifname)
        if not i or not i.get("ip"):
            return None
        try:
            return ipaddress.IPv4Interface(f'{i["ip"]}/{i["mask"]}')
        except ValueError:
            return None

    def link_at(self, nid, ifname):
        for (l, myif, _o, _oi) in self.adj.get(nid, []):
            if myif == ifname:
                return l
        return None

    def is_up(self, nid, ifname):
        l = self.link_at(nid, ifname)
        return bool(l and l["up"])

    def up_ifaces(self, nid):
        """[(ifname, IPv4Interface)] for interfaces that have a valid IP and a live link."""
        out = []
        for i in self.nodes[nid]["interfaces"]:
            ip = self.ipif(nid, i["name"])
            if ip and self.is_up(nid, i["name"]):
                out.append((i["name"], ip))
        return out

    def vlan(self, nid, ifname):
        i = self.iface(nid, ifname)
        return (i.get("vlan") if i else "") or "1"

    def owner_of_ip(self, ip):
        try:
            ip = ipaddress.IPv4Address(str(ip).strip())
        except ValueError:
            return None
        for n in self.topo["nodes"]:
            for i in n["interfaces"]:
                p = self.ipif(n["id"], i["name"])
                if p and p.ip == ip:
                    return n["id"], i["name"]
        return None

    def owns(self, nid, ip):
        for i in self.nodes[nid]["interfaces"]:
            p = self.ipif(nid, i["name"])
            if p and p.ip == ip:
                return True
        return False

    # ---- Layer 2 -------------------------------------------------------
    def l2_reach(self, nid, ifname, ignore_vlan=False):
        """End-device interfaces in the same broadcast domain as (nid, ifname).

        Switches are transparent. Frames only cross a switch between ports whose
        end devices share a VLAN (blank VLAN == VLAN 1). Links that are down are
        skipped. Returns {(peer_id, peer_if): (node_path, total_cost, total_delay)}.
        """
        out = {}
        first = next(((l, o, oi) for (l, mi, o, oi) in self.adj.get(nid, []) if mi == ifname), None)
        if not first or not first[0]["up"]:
            return out
        l0, oid, oif = first
        my_vlan = self.vlan(nid, ifname)
        q = deque([(oid, oif, [nid, oid], l0["cost"], l0["delay"], l0["id"])])
        seen_sw = set()
        while q:
            cur, cif, path, c, d, via = q.popleft()
            if self.nodes[cur]["type"] != "switch":
                if cur != nid and (ignore_vlan or self.vlan(cur, cif) == my_vlan):
                    out.setdefault((cur, cif), (path, c, d))
                continue
            if cur in seen_sw:
                continue
            seen_sw.add(cur)
            for (l, _mi, o2, oi2) in self.adj[cur]:
                if l["id"] == via or not l["up"]:
                    continue
                q.append((o2, oi2, path + [o2], c + l["cost"], d + l["delay"], l["id"]))
        return out

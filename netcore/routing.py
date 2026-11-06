"""Routing tables: connected + static + dynamic (OSPF-like or RIP-like).

OSPF  -> link-state style: Dijkstra over the router graph using link COST.
RIP   -> distance-vector style: shortest path by HOP COUNT, max 15 hops.
Route choice on a router = longest prefix match, then lowest administrative
distance (connected 0 < static 1 < OSPF 110 < RIP 120), then lowest metric.
"""
from __future__ import annotations

import heapq
import ipaddress
from dataclasses import dataclass

AD = {"C": 0, "S": 1, "O": 110, "R": 120}
RIP_MAX_HOPS = 15


@dataclass
class Route:
    net: ipaddress.IPv4Network
    kind: str                # C / S / O / R
    ad: int
    metric: float
    out_if: str
    next_hop: str | None = None
    via: str | None = None   # hostname of the next-hop router

    def to_dict(self):
        return {"dest": str(self.net), "kind": self.kind, "ad": self.ad, "metric": self.metric,
                "out_if": self.out_if, "next_hop": self.next_hop, "via": self.via}


def router_graph(net):
    """router -> [(neighbour router, my egress iface, neighbour's IP, segment cost)]"""
    g = {r: [] for r in net.routers()}
    for r in g:
        for ifn, ipif in net.up_ifaces(r):
            for (pn, pif), (_path, cost, _d) in net.l2_reach(r, ifn).items():
                if net.nodes[pn]["type"] != "router":
                    continue
                pip = net.ipif(pn, pif)
                if pip and pip.network == ipif.network:      # adjacency needs same subnet
                    g[r].append((pn, ifn, str(pip.ip), cost))
    return g


def shortest(g, src, weight):
    """Dijkstra. Returns (dist, first_hop) where first_hop[v] = (egress_if, next_hop_ip, neighbour)."""
    dist, first, done = {src: 0}, {src: None}, set()
    pq = [(0, src)]
    while pq:
        d, u = heapq.heappop(pq)
        if u in done:
            continue
        done.add(u)
        for (v, ifn, nh, c) in sorted(g[u], key=lambda e: (e[0], e[1])):
            nd = d + (1 if weight == "hops" else c)
            if v not in dist or nd < dist[v]:
                dist[v] = nd
                first[v] = (ifn, nh, v) if u == src else first[u]
                heapq.heappush(pq, (nd, v))
    return dist, first


def compute_tables(net):
    """{router_id: [Route, ...]} for the protocol selected in topology settings."""
    proto = net.protocol
    g = router_graph(net)
    origin = {r: {ip.network: ifn for ifn, ip in net.up_ifaces(r)} for r in g}
    tables = {}
    for r in g:
        routes = [Route(n, "C", 0, 0, ifn) for n, ifn in origin[r].items()]
        for sr in net.nodes[r]["static_routes"]:                    # manual routes
            try:
                dest = ipaddress.ip_network(sr["dest"], strict=False)
                nh = ipaddress.ip_address(sr["next_hop"])
            except ValueError:
                continue
            out = next((ifn for ifn, ip in net.up_ifaces(r) if nh in ip.network), None)
            owner = net.owner_of_ip(nh)
            if out and dest.version == 4:
                routes.append(Route(dest, "S", AD["S"], 0, out, str(nh), net.name(owner[0]) if owner else None))
        dist, first = shortest(g, r, "cost" if proto == "ospf" else "hops")
        best = {}
        for r2, d in dist.items():
            if r2 == r or (proto == "rip" and d > RIP_MAX_HOPS):
                continue
            for netw in origin[r2]:
                if netw in origin[r]:
                    continue
                if netw not in best or d < best[netw].metric:
                    ifn, nh, nb = first[r2]
                    kind = "O" if proto == "ospf" else "R"
                    best[netw] = Route(netw, kind, AD[kind], d, ifn, nh, net.name(nb))
        tables[r] = sorted(routes + list(best.values()), key=lambda x: (int(x.net.network_address), x.ad))
    return tables


def lookup(routes, dst_ip):
    cands = [r for r in routes if dst_ip in r.net]
    if not cands:
        return None
    return sorted(cands, key=lambda r: (-r.net.prefixlen, r.ad, r.metric))[0]


def find_loops(net, tables=None):
    """Follow next hops towards every known network; report cycles between routers."""
    tables = tables or compute_tables(net)
    nets = {r.net for rs in tables.values() for r in rs}
    loops, seen_sets = [], set()
    for start in tables:
        for n in nets:
            dst = next(n.hosts(), n.network_address)
            cur, trail = start, [start]
            for _ in range(64):
                route = lookup(tables.get(cur, []), dst)
                if not route or route.kind == "C" or not route.next_hop:
                    break
                owner = net.owner_of_ip(route.next_hop)
                if not owner or owner[0] not in tables:
                    break
                cur = owner[0]
                if cur in trail:
                    cyc = trail[trail.index(cur):]
                    key = frozenset(cyc)
                    if (key, str(n)) not in seen_sets:
                        seen_sets.add((key, str(n)))
                        loops.append({"routers": cyc, "names": [net.name(x) for x in cyc], "dest": str(n)})
                    break
                trail.append(cur)
    return loops

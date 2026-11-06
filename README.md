# NetForge — Web-Based Framework for Network Design, Configuration Validation and Communication Simulation

## Run it

```bash
pip install flask
python app.py          # then open http://localhost:5000
python -m unittest discover -s tests -v     # 17 tests
```

## What it does (one paragraph)

Draw a network in the browser (routers, switches, hosts, links), and NetForge **validates** the configuration
against 20+ networking rules, **computes routing tables** like OSPF or RIP would, and **simulates packets**
hop by hop (ARP, TTL, routing lookups, firewall ACLs, VLANs, failed links) with an animated path.

## Features

| Area | Feature |
|---|---|
| Design | Drag-and-drop canvas, auto port assignment, link cost/delay, drag to move, Delete key |
| Validation | 20+ rules with a suggested fix, offending devices highlighted in red, health score /100 |
| Routing | **OSPF** (Dijkstra on link cost) and **RIP** (hop count, 15-hop limit), static routes, administrative distance, longest-prefix match |
| Simulation | Ping (request **and** reply), traceroute with RTT, ARP, TTL, animated forward/return path |
| **Novelty** | **Compare RIP vs OSPF** on the same packet · **Link failure & automatic reroute** · **Firewall ACLs** with the blocking rule shown · **VLAN isolation** through switches · **Routing-loop detection** · **Reachability matrix** (all hosts pinging all hosts) · **Cisco-style config generator** · Subnet calculator |
| Persistence | Named projects in SQLite, JSON import/export, 5 example labs |
| Quality | Stateless API, 17 unit tests |

## Validation rules

dangling_link, bad_interface, link_down, invalid_ip, incomplete_interface, duplicate_ip, duplicate_hostname,
subnet_mismatch, vlan_mismatch, duplicate_subnet, overlapping_subnets, overlapping_interfaces, missing_gateway,
invalid_gateway, gateway_not_in_subnet, gateway_unreachable, gateway_not_router, invalid_static_route,
static_nexthop_unreachable, invalid_acl, isolated_node, l2_loop, partitioned_topology, routing_loop.

## Architecture

```
Browser (HTML/CSS/vanilla JS + SVG)  --JSON-->  Flask (app.py, stateless REST API)
                                                   |
                                  netcore/  model.py       topology model + Layer-2 reachability (VLAN-aware)
                                            validation.py  rules engine
                                            routing.py     OSPF/RIP tables, loop detection
                                            simulation.py  packet forwarding, ping, traceroute, matrix, compare
                                            configgen.py   Cisco-style config export
                                            store.py       SQLite projects
                                            templates_data.py  5 example labs
```

Every request carries the whole topology, so the server always works on exactly what is on screen.

## Key ideas (for the report)

- **Same subnet** → ARP, direct delivery (through switches). **Different subnet** → host sends to its default gateway; each router does a longest-prefix-match lookup and decrements TTL.
- **Ping succeeds only if the reply can return too** — so a missing route/gateway on the far side shows as a timeout, like real life.
- **OSPF vs RIP**: OSPF minimises total link *cost*; RIP minimises *hop count*. They can pick different paths (lab 2).
- **Switches are transparent**, but only pass frames between ports in the same **VLAN** (lab 5).
- **Route choice**: longest prefix → lowest administrative distance (connected 0, static 1, OSPF 110, RIP 120) → lowest metric.

## Review demo (about 6 minutes)

1. **Example labs → "1. Simple LAN"** → *Validate* (health 100) → select H1 → H3 → *Ping*. Say: same subnet, so ARP and direct delivery through the switch.
2. **"5. Find the bugs"** → *Validate*. Red rings show duplicate IP, wrong gateway, mask mismatch, VLAN mismatch, isolated host — each with a suggested fix.
3. **"2. RIP vs OSPF"** → From H1 To H2 → **Compare RIP vs OSPF**. RIP takes the 1-hop expensive link, OSPF takes the cheaper longer path. Then *Routing tables*.
4. **"3. Link failure & reroute"** → Ping H1 → H2 → click the R1–R3 link → *Take link DOWN* → Ping again: traffic reroutes via R2.
5. **"4. Firewall (ACL) lab"** → ping H1 → SRV works, H2 → SRV is blocked (red ring on R2, rule shown) → *Reachability matrix*.
6. *Configs* → shows generated Cisco-style config, including the access-list. Finish with the test suite: `python -m unittest`.

## Limitations (say these first)

IPv4 only; one broadcast domain per VLAN with simplified trunking (no STP, no real VLAN tagging); RIP/OSPF are
modelled by their path-selection rules, not by exchanging update packets or timers; only ICMP-style traffic.

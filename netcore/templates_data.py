"""Example labs. Each one is built to demonstrate a specific feature in a review."""


def N(nid, typ, name, x, y, ifaces, gateway="", acl=None, static=None):
    return {"id": nid, "type": typ, "hostname": name, "x": x, "y": y,
            "interfaces": [{"name": f"eth{k}", "ip": ip, "mask": m, "vlan": v} for k, (ip, m, v) in enumerate(ifaces)],
            "gateway": gateway, "static_routes": static or [], "acl": acl or []}


def L(lid, a, ia, b, ib, cost=1, delay=2, up=True):
    return {"id": lid, "nodeA": a, "ifaceA": f"eth{ia}", "nodeB": b, "ifaceB": f"eth{ib}",
            "cost": cost, "delay": delay, "up": up}


E = ("", "24", "")   # unused interface


def _lan():
    return {"settings": {"protocol": "ospf"}, "nodes": [
        N("n1", "router", "R1", 500, 110, [("192.168.1.1", "24", ""), E]),
        N("n2", "switch", "SW1", 500, 260, [E] * 4),
        N("n3", "host", "H1", 280, 440, [("192.168.1.10", "24", "")], "192.168.1.1"),
        N("n4", "host", "H2", 500, 440, [("192.168.1.11", "24", "")], "192.168.1.1"),
        N("n5", "host", "H3", 720, 440, [("192.168.1.12", "24", "")], "192.168.1.1"),
    ], "links": [L("l1", "n1", 0, "n2", 0), L("l2", "n2", 1, "n3", 0), L("l3", "n2", 2, "n4", 0), L("l4", "n2", 3, "n5", 0)]}


def _rip_vs_ospf():
    return {"settings": {"protocol": "ospf"}, "nodes": [
        N("n1", "router", "R1", 200, 300, [("10.0.1.1", "24", ""), ("192.168.14.1", "30", ""), ("192.168.12.1", "30", "")]),
        N("n2", "router", "R2", 400, 140, [("192.168.12.2", "30", ""), ("192.168.23.1", "30", "")]),
        N("n3", "router", "R3", 640, 140, [("192.168.23.2", "30", ""), ("192.168.34.1", "30", "")]),
        N("n4", "router", "R4", 840, 300, [("10.0.4.1", "24", ""), ("192.168.14.2", "30", ""), ("192.168.34.2", "30", "")]),
        N("n5", "host", "H1", 110, 500, [("10.0.1.10", "24", "")], "10.0.1.1"),
        N("n6", "host", "H2", 900, 500, [("10.0.4.10", "24", "")], "10.0.4.1"),
    ], "links": [L("l1", "n5", 0, "n1", 0), L("l2", "n1", 1, "n4", 1, cost=50, delay=8), L("l3", "n1", 2, "n2", 0),
                 L("l4", "n2", 1, "n3", 0), L("l5", "n3", 1, "n4", 2), L("l6", "n4", 0, "n6", 0)]}


def _failover():
    return {"settings": {"protocol": "ospf"}, "nodes": [
        N("n1", "router", "R1", 300, 230, [("10.0.1.1", "24", ""), ("192.168.13.1", "30", ""), ("192.168.12.1", "30", "")]),
        N("n2", "router", "R2", 500, 450, [("192.168.12.2", "30", ""), ("192.168.23.1", "30", "")]),
        N("n3", "router", "R3", 700, 230, [("192.168.13.2", "30", ""), ("192.168.23.2", "30", ""), ("10.0.3.1", "24", "")]),
        N("n4", "host", "H1", 130, 230, [("10.0.1.10", "24", "")], "10.0.1.1"),
        N("n5", "host", "H2", 870, 230, [("10.0.3.10", "24", "")], "10.0.3.1"),
    ], "links": [L("l1", "n4", 0, "n1", 0), L("l2", "n1", 1, "n3", 0), L("l3", "n1", 2, "n2", 0),
                 L("l4", "n2", 1, "n3", 1), L("l5", "n3", 2, "n5", 0)]}


def _acl_lab():
    deny = [{"action": "deny", "src": "10.0.1.20", "dst": "10.0.2.10"}]
    return {"settings": {"protocol": "ospf"}, "nodes": [
        N("n1", "router", "R1", 330, 250, [("10.0.1.1", "24", ""), ("172.16.0.1", "30", "")]),
        N("n2", "router", "R2", 670, 250, [("172.16.0.2", "30", ""), ("10.0.2.1", "24", "")], acl=deny),
        N("n3", "switch", "SW1", 160, 250, [E] * 3),
        N("n4", "switch", "SW2", 840, 250, [E] * 3),
        N("n5", "host", "H1", 90, 430, [("10.0.1.10", "24", "")], "10.0.1.1"),
        N("n6", "host", "H2", 240, 430, [("10.0.1.20", "24", "")], "10.0.1.1"),
        N("n7", "host", "SRV", 760, 430, [("10.0.2.10", "24", "")], "10.0.2.1"),
        N("n8", "host", "H3", 910, 430, [("10.0.2.20", "24", "")], "10.0.2.1"),
    ], "links": [L("l1", "n3", 0, "n1", 0), L("l2", "n1", 1, "n2", 0), L("l3", "n2", 1, "n4", 0),
                 L("l4", "n3", 1, "n5", 0), L("l5", "n3", 2, "n6", 0), L("l6", "n4", 1, "n7", 0), L("l7", "n4", 2, "n8", 0)]}


def _debug_me():
    return {"settings": {"protocol": "ospf"}, "nodes": [
        N("n1", "router", "R1", 480, 130, [("10.0.0.1", "24", ""), ("10.0.1.1", "24", "")]),
        N("n2", "switch", "SW1", 250, 280, [E] * 5),
        N("n3", "switch", "SW2", 720, 280, [E] * 4),
        N("n4", "host", "H1", 100, 450, [("10.0.0.10", "24", "")], "10.0.0.1"),
        N("n5", "host", "H2", 250, 450, [("10.0.0.10", "24", "")], "10.0.0.254"),
        N("n6", "host", "H3", 400, 450, [("10.0.0.50", "16", "")], "10.0.0.1"),
        N("n7", "host", "H4", 640, 450, [("10.0.1.5", "24", "10")], "10.0.1.1"),
        N("n8", "host", "H5", 800, 450, [("10.0.1.6", "24", "20")], "10.0.1.1"),
        N("n9", "host", "H6", 900, 130, [("10.0.9.9", "24", "")], "10.0.9.1"),
    ], "links": [L("l1", "n1", 0, "n2", 0), L("l2", "n1", 1, "n3", 0), L("l3", "n2", 1, "n4", 0),
                 L("l4", "n2", 2, "n5", 0), L("l5", "n2", 3, "n6", 0), L("l6", "n3", 1, "n7", 0), L("l7", "n3", 2, "n8", 0)]}


TEMPLATES = {
    "lan": {"name": "1. Simple LAN", "description": "Router + switch + 3 hosts. A clean network: validation passes, pings work.",
            "topology": _lan()},
    "rip_vs_ospf": {"name": "2. RIP vs OSPF", "description": "Direct link R1-R4 is 1 hop but expensive (cost 50). RIP takes it, OSPF goes around via R2/R3. Use 'Compare RIP vs OSPF'.",
                    "topology": _rip_vs_ospf()},
    "failover": {"name": "3. Link failure & reroute", "description": "Ping H1 to H2, then click link R1-R3, take it DOWN and ping again: traffic reroutes via R2.",
                 "topology": _failover()},
    "acl_lab": {"name": "4. Firewall (ACL) lab", "description": "R2 has an ACL blocking H2 from reaching SRV. H1 to SRV works, H2 to SRV is blocked.",
                "topology": _acl_lab()},
    "debug_me": {"name": "5. Find the bugs", "description": "Deliberately broken: duplicate IP, bad gateway, mask mismatch, VLAN mismatch, isolated host. Run Validation.",
                 "topology": _debug_me()},
}


def list_templates():
    return [{"key": k, "name": v["name"], "description": v["description"]} for k, v in TEMPLATES.items()]

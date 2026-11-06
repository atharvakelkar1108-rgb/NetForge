import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from netcore import simulation as sim
from netcore.model import Net, normalise
from netcore.routing import compute_tables, find_loops
from netcore.templates_data import TEMPLATES
from netcore.validation import validate


def topo(key):
    return normalise(copy.deepcopy(TEMPLATES[key]["topology"]))


def node(t, host):
    return next(n for n in t["nodes"] if n["hostname"] == host)


def ip(t, host):
    return next(i["ip"] for i in node(t, host)["interfaces"] if i["ip"])


def ping(t, a, b):
    net = Net(t)
    return sim.ping(net, compute_tables(net), ip(t, a), ip(t, b))


def rules(t):
    return {i["rule"] for i in validate(t)["issues"]}


class ValidationTests(unittest.TestCase):
    def test_clean_lan_has_no_errors(self):
        r = validate(topo("lan"))
        self.assertTrue(r["ok"], r["issues"])

    def test_unused_interface_is_not_an_error(self):
        self.assertNotIn("incomplete_interface", rules(topo("lan")))

    def test_debug_lab_finds_every_planted_bug(self):
        found = rules(topo("debug_me"))
        for rule in ("duplicate_ip", "gateway_unreachable", "subnet_mismatch", "vlan_mismatch", "isolated_node"):
            self.assertIn(rule, found)

    def test_cidr_typed_in_ip_box_is_split(self):
        t = topo("lan")
        node(t, "H1")["interfaces"][0]["ip"] = "192.168.1.10/24"
        t = normalise(t)
        self.assertEqual(node(t, "H1")["interfaces"][0]["ip"], "192.168.1.10")
        self.assertTrue(validate(t)["ok"])

    def test_dangling_link_detected(self):
        t = topo("lan")
        t["nodes"] = [n for n in t["nodes"] if n["hostname"] != "H3"]
        self.assertIn("dangling_link", rules(t))


class PingTests(unittest.TestCase):
    def test_same_subnet_through_switch(self):
        self.assertTrue(ping(topo("lan"), "H1", "H3")["success"])

    def test_cross_router_ping_works_both_protocols(self):
        for proto in ("ospf", "rip"):
            t = topo("rip_vs_ospf")
            t["settings"]["protocol"] = proto
            self.assertTrue(ping(t, "H1", "H2")["success"], proto)

    def test_vlan_isolation_blocks_same_subnet_hosts(self):
        t = topo("lan")
        node(t, "H2")["interfaces"][0]["vlan"] = "20"
        r = ping(t, "H1", "H2")
        self.assertFalse(r["success"])
        self.assertEqual(r["code"], "arp")

    def test_missing_gateway_breaks_reply_path(self):
        t = topo("rip_vs_ospf")
        node(t, "H2")["gateway"] = ""
        r = ping(t, "H1", "H2")
        self.assertFalse(r["success"])
        self.assertTrue(r["code"].startswith("reply_"))

    def test_acl_blocks_only_the_denied_host(self):
        t = topo("acl_lab")
        self.assertTrue(ping(t, "H1", "SRV")["success"])
        r = ping(t, "H2", "SRV")
        self.assertFalse(r["success"])
        self.assertEqual(r["code"], "acl")
        self.assertTrue(ping(t, "H2", "H3")["success"])


class RoutingTests(unittest.TestCase):
    def test_rip_and_ospf_choose_different_paths(self):
        t = topo("rip_vs_ospf")
        rip, ospf = sim.compare(t, ip(t, "H1"), ip(t, "H2"))
        self.assertNotIn("R2", rip["names"])
        self.assertIn("R2", ospf["names"])
        self.assertLess(ospf["cost"], rip["cost"])

    def test_failover_reroutes_via_r2(self):
        t = topo("failover")
        self.assertNotIn("R2", ping(t, "H1", "H2") and sim.compare(t, ip(t, "H1"), ip(t, "H2"))[1]["names"])
        next(l for l in t["links"] if l["id"] == "l2")["up"] = False
        after = sim.compare(t, ip(t, "H1"), ip(t, "H2"))[1]
        self.assertTrue(after["ok"])
        self.assertIn("R2", after["names"])

    def test_both_paths_down_is_unreachable(self):
        t = topo("failover")
        for l in t["links"]:
            if l["id"] in ("l2", "l3"):
                l["up"] = False
        self.assertFalse(ping(t, "H1", "H2")["success"])

    def test_static_route_loop_detected_and_caught_in_simulation(self):
        t = topo("rip_vs_ospf")
        node(t, "R2")["static_routes"] = [{"dest": "10.0.4.0/24", "next_hop": "192.168.23.2"}]
        node(t, "R3")["static_routes"] = [{"dest": "10.0.4.0/24", "next_hop": "192.168.23.1"}]
        net = Net(t)
        self.assertTrue(find_loops(net, compute_tables(net)))
        self.assertIn("routing_loop", rules(t))
        self.assertEqual(ping(t, "H1", "H2")["code"], "loop")

    def test_rip_hop_limit(self):
        # chain of 17 routers: far end is beyond RIP's 15-hop limit
        nodes, links = [], []
        for k in range(17):
            ifs = [{"name": "eth0", "ip": f"10.{k}.0.2" if k else "", "mask": "30", "vlan": ""},
                   {"name": "eth1", "ip": f"10.{k + 1}.0.1", "mask": "30", "vlan": ""}]
            if k == 0:
                ifs[0]["ip"] = "10.100.0.1"
            nodes.append({"id": f"r{k}", "type": "router", "hostname": f"R{k}", "interfaces": ifs})
            if k:
                links.append({"id": f"l{k}", "nodeA": f"r{k - 1}", "ifaceA": "eth1", "nodeB": f"r{k}", "ifaceB": "eth0"})
        t = normalise({"nodes": nodes, "links": links, "settings": {"protocol": "rip"}})
        net = Net(t)
        tables = compute_tables(net)
        learned = {str(r.net) for r in tables["r0"] if r.kind == "R"}
        self.assertNotIn("10.17.0.0/30", learned)
        self.assertIn("10.3.0.0/30", learned)


class ToolTests(unittest.TestCase):
    def test_matrix_symmetry_and_acl_effect(self):
        t = topo("acl_lab")
        net = Net(t)
        m = sim.matrix(net, compute_tables(net))
        i, j = m["labels"].index("H2"), m["labels"].index("SRV")
        self.assertFalse(m["matrix"][i][j])

    def test_config_generation(self):
        from netcore.configgen import generate
        cfg = generate(topo("acl_lab"))["configs"]
        self.assertIn("access-list 101 deny ip host 10.0.1.20 host 10.0.2.10", cfg["R2"])
        self.assertIn("router ospf 1", cfg["R1"])
        self.assertIn("ip default-gateway 10.0.1.1", cfg["H1"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

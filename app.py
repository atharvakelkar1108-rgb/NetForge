"""NetForge - web-based network design, configuration validation and communication simulation.

Run:  pip install flask   then   python app.py   ->  http://localhost:5000

The API is STATELESS: every request carries the topology currently on screen,
so results always match what the user sees (no 'save first' step, no stale data).
"""
import ipaddress

from flask import Flask, jsonify, render_template, request

from netcore import simulation as sim
from netcore import store
from netcore.configgen import generate
from netcore.model import Net, normalise
from netcore.routing import compute_tables, find_loops
from netcore.templates_data import TEMPLATES, list_templates
from netcore.validation import validate

app = Flask(__name__)


def _ctx():
    data = request.get_json(force=True, silent=True) or {}
    topo = normalise(data.get("topology"))
    net = Net(topo)
    return data, topo, net


@app.route("/")
def index():
    return render_template("index.html")


@app.post("/api/validate")
def api_validate():
    _, topo, _n = _ctx()
    return jsonify(validate(topo))


@app.post("/api/routing")
def api_routing():
    _, topo, net = _ctx()
    tables = compute_tables(net)
    return jsonify({
        "protocol": net.protocol,
        "tables": [{"id": r, "hostname": net.name(r), "routes": [x.to_dict() for x in tables[r]]} for r in tables],
        "loops": find_loops(net, tables),
    })


@app.post("/api/ping")
def api_ping():
    d, _t, net = _ctx()
    return jsonify(sim.ping(net, compute_tables(net), d.get("src"), d.get("dst")))


@app.post("/api/traceroute")
def api_trace():
    d, _t, net = _ctx()
    return jsonify(sim.traceroute(net, compute_tables(net), d.get("src"), d.get("dst")))


@app.post("/api/matrix")
def api_matrix():
    _, _t, net = _ctx()
    return jsonify(sim.matrix(net, compute_tables(net)))


@app.post("/api/compare")
def api_compare():
    d, topo, _n = _ctx()
    return jsonify(sim.compare(topo, d.get("src"), d.get("dst")))


@app.post("/api/configs")
def api_configs():
    _, topo, _n = _ctx()
    return jsonify(generate(topo))


@app.post("/api/subnet")
def api_subnet():
    cidr = (request.get_json(force=True, silent=True) or {}).get("cidr", "")
    try:
        i = ipaddress.IPv4Interface(cidr.strip() if "/" in cidr else cidr.strip() + "/24")
    except ValueError:
        return jsonify({"error": "Enter an IPv4 address with a mask, e.g. 192.168.10.37/26"}), 400
    n = i.network
    hosts = max(n.num_addresses - 2, 0) if n.prefixlen < 31 else n.num_addresses
    first = n.network_address + 1 if n.prefixlen < 31 else n.network_address
    last = n.broadcast_address - 1 if n.prefixlen < 31 else n.broadcast_address
    return jsonify({"Address": str(i.ip), "Network": str(n), "Subnet mask": str(n.netmask), "Wildcard": str(n.hostmask),
                    "Broadcast": str(n.broadcast_address), "First usable": str(first), "Last usable": str(last),
                    "Usable hosts": hosts, "Private range": "yes" if i.ip.is_private else "no"})


@app.get("/api/templates")
def api_templates():
    return jsonify(list_templates())


@app.get("/api/templates/<key>")
def api_template(key):
    t = TEMPLATES.get(key)
    if not t:
        return jsonify({"error": "unknown template"}), 404
    return jsonify(t)


@app.get("/api/projects")
def api_projects():
    return jsonify(store.list_projects())


@app.post("/api/projects")
def api_save_project():
    d = request.get_json(force=True, silent=True) or {}
    name = (d.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Project needs a name"}), 400
    store.save_project(name, d.get("topology") or {})
    return jsonify({"saved": name})


@app.get("/api/projects/<name>")
def api_load_project(name):
    t = store.load_project(name)
    return (jsonify({"name": name, "topology": t}) if t is not None else (jsonify({"error": "not found"}), 404))


@app.delete("/api/projects/<name>")
def api_delete_project(name):
    store.delete_project(name)
    return jsonify({"deleted": name})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)

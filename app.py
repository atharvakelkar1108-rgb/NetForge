"""NetForge - web-based network design, configuration validation and communication simulation.

Run:  pip install flask   then   python app.py   ->  http://localhost:5000

Pages:   /  (home)   /login   /register   /app (the interface, needs login)
The JSON API is STATELESS: every request carries the topology on screen, so
results always match what the user sees. All /api/* routes need a signed-in user.
"""
import ipaddress
import os
import re
import secrets
import time
from collections import defaultdict
from datetime import timedelta

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from netcore import simulation as sim
from netcore import store
from netcore.configgen import generate
from netcore.model import Net, normalise
from netcore.routing import compute_tables, find_loops
from netcore.templates_data import TEMPLATES, list_templates
from netcore.validation import validate

BASE = os.path.dirname(os.path.abspath(__file__))


def _secret_key():
    """Session signing key: env var, else a random key saved next to the app so logins survive restarts."""
    if os.environ.get("NETFORGE_SECRET"):
        return os.environ["NETFORGE_SECRET"]
    path = os.path.join(BASE, ".secret_key")
    if os.path.exists(path):
        return open(path).read().strip()
    key = secrets.token_hex(32)
    with open(path, "w") as f:
        f.write(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


app = Flask(__name__)
app.secret_key = _secret_key()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  PERMANENT_SESSION_LIFETIME=timedelta(days=7))

# ============================== authentication ================================
USER_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
MIN_PW = 6
FAILS, WINDOW, MAX_FAILS = defaultdict(list), 300, 5          # simple brute-force throttle
_DUMMY_HASH = generate_password_hash("not-a-real-password")    # keeps timing equal for unknown users


def _throttled(key):
    now = time.time()
    FAILS[key] = [t for t in FAILS[key] if now - t < WINDOW]
    return len(FAILS[key]) >= MAX_FAILS


def _safe_next(target):
    """Only allow redirects to paths on this site (blocks open-redirect tricks)."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return url_for("interface")


def _login(user, remember):
    session.clear()                      # fresh session on login (prevents session fixation)
    session["uid"], session["user"] = user["id"], user["username"]
    session.permanent = bool(remember)


@app.before_request
def require_login():
    if request.path == "/app" or request.path.startswith("/api/"):
        if not session.get("uid"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "login required"}), 401
            return redirect(url_for("login", next=request.path))


@app.get("/")
def home():
    return render_template("home.html", user=session.get("user"))


@app.route("/login", methods=["GET", "POST"])
def login():
    nxt = request.values.get("next", "")
    if request.method == "GET":
        if session.get("uid"):
            return redirect(_safe_next(nxt))
        return render_template("auth.html", mode="login", next=nxt)
    username, pw = request.form.get("username", "").strip(), request.form.get("password", "")
    key = (request.remote_addr, username.lower())
    if _throttled(key):
        return render_template("auth.html", mode="login", next=nxt, username=username,
                               error="Too many failed attempts. Wait a few minutes and try again."), 429
    user = store.get_user_by_name(username) if username else None
    ok = check_password_hash(user["pw_hash"] if user else _DUMMY_HASH, pw)
    if not (user and ok):
        FAILS[key].append(time.time())
        return render_template("auth.html", mode="login", next=nxt, username=username,
                               error="Incorrect username or password."), 401
    FAILS.pop(key, None)
    _login(user, request.form.get("remember"))
    return redirect(_safe_next(nxt))


@app.route("/register", methods=["GET", "POST"])
def register():
    nxt = request.values.get("next", "")
    if request.method == "GET":
        return render_template("auth.html", mode="register", next=nxt)
    username, pw, pw2 = request.form.get("username", "").strip(), request.form.get("password", ""), request.form.get("confirm", "")
    error = None
    if not USER_RE.match(username):
        error = "Username must be 3-32 characters: letters, numbers, dot, dash or underscore."
    elif len(pw) < MIN_PW:
        error = f"Password must be at least {MIN_PW} characters."
    elif pw != pw2:
        error = "The two passwords do not match."
    else:
        uid = store.create_user(username, generate_password_hash(pw))
        if uid is None:
            error = "That username is already taken."
    if error:
        return render_template("auth.html", mode="register", next=nxt, username=username, error=error), 400
    _login({"id": uid, "username": username}, True)
    return redirect(_safe_next(nxt))


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.get("/app")
def interface():
    return render_template("index.html", user=session["user"])


# ============================== simulation API ================================
def _ctx():
    data = request.get_json(force=True, silent=True) or {}
    topo = normalise(data.get("topology"))
    return data, topo, Net(topo)


@app.post("/api/validate")
def api_validate():
    _, topo, _n = _ctx()
    return jsonify(validate(topo))


@app.post("/api/routing")
def api_routing():
    _, _t, net = _ctx()
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
    return jsonify(t) if t else (jsonify({"error": "unknown template"}), 404)


# ---- saved projects: each user only ever sees their own ---------------------------
@app.get("/api/projects")
def api_projects():
    return jsonify(store.list_projects(session["uid"]))


@app.post("/api/projects")
def api_save_project():
    d = request.get_json(force=True, silent=True) or {}
    name = (d.get("name") or "").strip()[:80]
    if not name:
        return jsonify({"error": "Project needs a name"}), 400
    store.save_project(session["uid"], name, d.get("topology") or {})
    return jsonify({"saved": name})


@app.get("/api/projects/<name>")
def api_load_project(name):
    t = store.load_project(session["uid"], name)
    return jsonify({"name": name, "topology": t}) if t is not None else (jsonify({"error": "not found"}), 404)


@app.delete("/api/projects/<name>")
def api_delete_project(name):
    store.delete_project(session["uid"], name)
    return jsonify({"deleted": name})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
"use strict";
// ============================================================================
// NetForge front end. The browser owns the topology; every API call sends the
// whole thing, so the backend always validates/simulates exactly what is shown.
// ============================================================================
const NS = "http://www.w3.org/2000/svg";
const $ = id => document.getElementById(id);
const PORTS = { router: 4, switch: 8, host: 1 };
const PREFIX = { router: "R", switch: "SW", host: "H" };

let state = { name: "Untitled project", nodes: [], links: [], settings: { protocol: "ospf" } };
let mode = { place: null, link: false, first: null };
let sel = null;                                   // {kind:'node'|'link', id}
let overlay = { issueNodes: new Set(), issueLinks: new Set(), failedAt: null };
let drag = null, suppressClick = false, animating = false;

// ---------- helpers ---------------------------------------------------------
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nodeById = id => state.nodes.find(n => n.id === id);
const linkById = id => state.links.find(l => l.id === id);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
function el(tag, attrs, parent) { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; }
function uid(prefix, list) { let i = 1; while (list.some(x => x.id === prefix + i)) i++; return prefix + i; }
function nextName(type) { let i = 1; while (state.nodes.some(n => n.hostname === PREFIX[type] + i)) i++; return PREFIX[type] + i; }
const bareIp = v => String(v || "").split("/")[0].trim();
const validIp = v => /^\d{1,3}(\.\d{1,3}){3}$/.test(v);

function log(text, cls = "") {
  const d = document.createElement("div"); d.className = "console-line " + cls; d.textContent = text;
  $("console-body").appendChild(d); $("console-body").scrollTop = 1e9;
}
const logLines = arr => arr.forEach(x => log(x.t, x.c));

async function api(path, extra = {}) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ topology: state, ...extra }) });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
  return res.json();
}
const getJson = async p => (await fetch(p)).json();

// ---------- modal -----------------------------------------------------------
function modal(title, html) { $("modal-title").textContent = title; $("modal-body").innerHTML = html; $("modal").classList.remove("hidden"); return $("modal-body"); }
const closeModal = () => $("modal").classList.add("hidden");
$("modal-close").onclick = closeModal;
$("modal").addEventListener("pointerdown", e => { if (e.target.id === "modal") closeModal(); });

// ---------- tools / modes ---------------------------------------------------
function setLink(on) {
  mode.link = on; mode.first = null;
  $("btn-link-mode").classList.toggle("active", on);
  $("canvas").classList.toggle("linking", on);
  if (on) { mode.place = null; document.querySelectorAll(".tool[data-type]").forEach(b => b.classList.remove("active")); $("canvas").classList.remove("placing"); }
  renderCanvas();
}
document.querySelectorAll(".tool[data-type]").forEach(btn => btn.onclick = () => {
  const t = btn.dataset.type, same = mode.place === t;
  document.querySelectorAll(".tool[data-type]").forEach(b => b.classList.remove("active"));
  mode.place = same ? null : t;
  if (!same) { btn.classList.add("active"); setLink(false); }
  $("canvas").classList.toggle("placing", !!mode.place);
});
$("btn-link-mode").onclick = () => setLink(!mode.link);
document.addEventListener("keydown", e => {
  if (e.key === "Escape") { mode.place = null; $("canvas").classList.remove("placing"); document.querySelectorAll(".tool[data-type]").forEach(b => b.classList.remove("active")); setLink(false); closeModal(); }
  if ((e.key === "Delete" || e.key === "Backspace") && sel && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) deleteSelected();
});

function toSvg(e) {
  const r = $("canvas").getBoundingClientRect(), vb = $("canvas").viewBox.baseVal;
  // viewBox is scaled with 'meet' - account for letterboxing
  const s = Math.min(r.width / vb.width, r.height / vb.height);
  const ox = (r.width - vb.width * s) / 2, oy = (r.height - vb.height * s) / 2;
  return { x: (e.clientX - r.left - ox) / s, y: (e.clientY - r.top - oy) / s };
}

$("canvas").addEventListener("click", e => {
  if (suppressClick) { suppressClick = false; return; }
  if (mode.place) {
    const p = toSvg(e), type = mode.place;
    const n = { id: uid("n", state.nodes), type, hostname: nextName(type), x: clamp(p.x, 30, 970), y: clamp(p.y, 30, 660),
      interfaces: Array.from({ length: PORTS[type] }, (_, i) => ({ name: "eth" + i, ip: "", mask: "24", vlan: "" })),
      gateway: "", static_routes: [], acl: [] };
    state.nodes.push(n); select({ kind: "node", id: n.id }); renderSim();
  } else if (!mode.link) select(null);
});

// ---------- links -----------------------------------------------------------
function freeIface(n) {
  const used = new Set(state.links.flatMap(l => [l.nodeA === n.id && l.ifaceA, l.nodeB === n.id && l.ifaceB]));
  let f = n.interfaces.find(i => !used.has(i.name));
  if (!f) { f = { name: "eth" + n.interfaces.length, ip: "", mask: "24", vlan: "" }; n.interfaces.push(f); log(`${n.hostname}: all ports in use, added ${f.name}.`, "muted"); }
  return f.name;
}
function linkClick(n) {
  if (!mode.first) { mode.first = n.id; log(`Link: now click the device to connect to ${n.hostname}.`, "muted"); renderCanvas(); return; }
  if (mode.first === n.id) return;
  const a = nodeById(mode.first), b = n;
  if (state.links.some(l => (l.nodeA === a.id && l.nodeB === b.id) || (l.nodeA === b.id && l.nodeB === a.id))) {
    log(`${a.hostname} and ${b.hostname} are already linked.`, "warn");
  } else {
    const l = { id: uid("l", state.links), nodeA: a.id, ifaceA: freeIface(a), nodeB: b.id, ifaceB: freeIface(b), cost: 1, delay: 2, up: true };
    state.links.push(l); log(`Linked ${a.hostname}:${l.ifaceA} <-> ${b.hostname}:${l.ifaceB}`, "ok");
  }
  setLink(false); render();
}
function deleteSelected() {
  if (!sel) return;
  if (sel.kind === "link") { state.links = state.links.filter(l => l.id !== sel.id); log("Link removed.", "warn"); }
  else { const n = nodeById(sel.id); state.links = state.links.filter(l => l.nodeA !== sel.id && l.nodeB !== sel.id); state.nodes = state.nodes.filter(x => x.id !== sel.id); if (n) log(`Removed ${n.hostname}.`, "warn"); }
  sel = null; render();
}

// ---------- canvas rendering ------------------------------------------------
function render() { renderCanvas(); renderProps(); renderSim(); }
function select(s) { sel = s; renderCanvas(); renderProps(); }

function renderCanvas() {
  const LL = $("links-layer"), NL = $("nodes-layer"); LL.innerHTML = ""; NL.innerHTML = "";
  $("canvas-empty").style.display = state.nodes.length ? "none" : "flex";
  for (const l of state.links) {
    const a = nodeById(l.nodeA), b = nodeById(l.nodeB); if (!a || !b) continue;
    const g = el("g", { class: "link-g" }, LL), isSel = sel && sel.kind === "link" && sel.id === l.id;
    el("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: ["link-line", l.up === false && "down", isSel && "sel", overlay.issueLinks.has(l.id) && "issue"].filter(Boolean).join(" ") }, g);
    const hit = el("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: "link-hit" }, g);
    hit.addEventListener("click", e => { e.stopPropagation(); if (!mode.link) select({ kind: "link", id: l.id }); });
    if (l.up === false || l.cost != 1 || isSel) {
      const t = el("text", { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 - 7, class: "link-label" + (l.up === false ? " down" : "") }, g);
      t.textContent = l.up === false ? "✕ DOWN" : "cost " + l.cost;
    }
  }
  for (const n of state.nodes) {
    const g = el("g", { class: "node-group" + (sel && sel.kind === "node" && sel.id === n.id ? " selected" : "") }, NL);
    let r;
    if (n.type === "router") { r = 24; el("polygon", { points: `${n.x},${n.y - r} ${n.x + r},${n.y} ${n.x},${n.y + r} ${n.x - r},${n.y}`, class: "node-shape" }, g); }
    else if (n.type === "switch") { r = 22; el("rect", { x: n.x - r, y: n.y - 14, width: r * 2, height: 28, rx: 4, class: "node-shape" }, g); }
    else { r = 17; el("circle", { cx: n.x, cy: n.y, r, class: "node-shape" }, g); }
    const ring = (cls, extra = 8) => el("circle", { cx: n.x, cy: n.y, r: r + extra, class: cls }, g);
    if (overlay.issueNodes.has(n.id)) ring("ring-issue");
    if (mode.first === n.id) ring("ring-first");
    if (overlay.failedAt === n.id) ring("ring-fail", 11);
    el("text", { x: n.x, y: n.y + r + 15, class: "node-label" }, g).textContent = n.hostname;
    const ip = n.type === "switch" ? "" : bareIp((n.interfaces.find(i => i.ip) || {}).ip);
    if (ip) el("text", { x: n.x, y: n.y + r + 27, class: "node-sub" }, g).textContent = ip;
    if (n.acl && n.acl.length) el("text", { x: n.x, y: n.y - r - 6, class: "badge" }, g).textContent = "ACL";
    g.addEventListener("pointerdown", e => {
      e.stopPropagation(); suppressClick = true;
      if (mode.link) return linkClick(n);
      if (mode.place) { suppressClick = false; return; }
      select({ kind: "node", id: n.id });
      const p = toSvg(e); drag = { id: n.id, dx: p.x - n.x, dy: p.y - n.y };
    });
  }
}
window.addEventListener("pointermove", e => {
  if (!drag) return; const n = nodeById(drag.id), p = toSvg(e);
  n.x = clamp(p.x - drag.dx, 30, 970); n.y = clamp(p.y - drag.dy, 30, 660); renderCanvas();
});
window.addEventListener("pointerup", () => { drag = null; setTimeout(() => suppressClick = false, 0); });

// ---------- properties panel ------------------------------------------------
const linkedTo = (n, ifn) => { const l = state.links.find(x => (x.nodeA === n.id && x.ifaceA === ifn) || (x.nodeB === n.id && x.ifaceB === ifn)); if (!l) return "not connected"; const o = nodeById(l.nodeA === n.id ? l.nodeB : l.nodeA); return "→ " + (o ? o.hostname : "?") + (l.up === false ? " (down)" : ""); };

function renderProps() {
  const p = $("properties");
  if (!sel) { p.innerHTML = `<div class="panel-title">Properties</div><div class="empty-state">Select a device or a link.<br><br>• Drag devices to move them<br>• Click a link to change its cost/delay or take it <b>down</b> to test failover<br>• Press Delete to remove the selection</div>`; return; }
  if (sel.kind === "link") return linkProps(p);
  const n = nodeById(sel.id); if (!n) { sel = null; return renderProps(); }
  let h = `<div class="panel-title">${n.type} · ${esc(n.hostname)}</div><label class="field-label">Hostname</label><input class="field" id="p-host" value="${esc(n.hostname)}"><div class="panel-title sub">Interfaces</div>`;
  if (n.type === "switch") {
    h += `<div class="hint" style="margin:0 0 6px">Layer-2 device - no IPs. It passes frames only between ports whose devices share a VLAN (set the VLAN on the host/router interface).</div>`;
    n.interfaces.forEach(i => h += `<div class="port-row"><span class="mono">${i.name}</span><span class="muted">${esc(linkedTo(n, i.name))}</span></div>`);
  } else n.interfaces.forEach((i, k) => h += `<div class="iface-row"><div class="iface-row-title">${i.name} <span class="muted">${esc(linkedTo(n, i.name))}</span></div>
      <div class="grid3"><div><label class="field-label">IP</label><input class="field" data-if="ip" data-i="${k}" value="${esc(i.ip)}" placeholder="10.0.0.1"></div>
      <div><label class="field-label">Mask</label><input class="field" data-if="mask" data-i="${k}" value="${esc(i.mask)}"></div>
      <div><label class="field-label">VLAN</label><input class="field" data-if="vlan" data-i="${k}" value="${esc(i.vlan)}" placeholder="1"></div></div></div>`);
  if (n.type === "host") h += `<label class="field-label">Default gateway</label><input class="field" id="p-gw" value="${esc(n.gateway)}" placeholder="router IP on this subnet">`;
  if (n.type === "router") {
    h += `<div class="panel-title sub">Static routes <span class="muted">(override OSPF/RIP)</span></div>`;
    n.static_routes.forEach((r, k) => h += `<div class="rule-row"><div class="rule-top"><input class="field" data-sr="dest" data-i="${k}" value="${esc(r.dest)}" placeholder="10.0.2.0/24"><button class="x" data-delsr="${k}">✕</button></div><input class="field" data-sr="next_hop" data-i="${k}" value="${esc(r.next_hop)}" placeholder="next hop IP"></div>`);
    h += `<button class="btn-small" id="add-sr">+ static route</button>`;
    h += `<div class="panel-title sub">Firewall ACL <span class="muted">(first match wins)</span></div>`;
    n.acl.forEach((r, k) => h += `<div class="rule-row"><div class="rule-top"><select class="field" data-acl="action" data-i="${k}"><option value="permit"${r.action !== "deny" ? " selected" : ""}>permit</option><option value="deny"${r.action === "deny" ? " selected" : ""}>deny</option></select><button class="x" data-delacl="${k}">✕</button></div>
      <div class="grid2"><input class="field" data-acl="src" data-i="${k}" value="${esc(r.src)}" placeholder="src: any"><input class="field" data-acl="dst" data-i="${k}" value="${esc(r.dst)}" placeholder="dst: any"></div></div>`);
    h += `<button class="btn-small" id="add-acl">+ ACL rule</button><div class="hint">Src/dst: <span class="mono">any</span>, a host (10.0.1.20) or CIDR (10.0.1.0/24). No match = permit.</div>`;
  }
  h += `<br><button class="btn-small danger" id="p-del">Delete device</button>`;
  p.innerHTML = h;

  $("p-host").oninput = e => { n.hostname = e.target.value; renderCanvas(); renderSim(); };
  p.querySelectorAll("[data-if]").forEach(inp => {
    inp.oninput = () => { n.interfaces[+inp.dataset.i][inp.dataset.if] = inp.value; renderCanvas(); renderSim(); };
    if (inp.dataset.if === "ip") inp.onblur = () => {                   // 10.0.0.2/24 typed in IP box -> split
      if (inp.value.includes("/")) { const [a, m] = inp.value.split("/"), i = n.interfaces[+inp.dataset.i]; i.ip = a.trim(); if (m.trim()) i.mask = m.trim(); renderProps(); renderCanvas(); renderSim(); }
    };
  });
  if ($("p-gw")) $("p-gw").oninput = e => n.gateway = e.target.value;
  p.querySelectorAll("[data-sr]").forEach(i => i.oninput = () => n.static_routes[+i.dataset.i][i.dataset.sr] = i.value);
  p.querySelectorAll("[data-acl]").forEach(i => { const f = () => { n.acl[+i.dataset.i][i.dataset.acl] = i.value; }; i.oninput = f; i.onchange = f; });
  p.querySelectorAll("[data-delsr]").forEach(b => b.onclick = () => { n.static_routes.splice(+b.dataset.delsr, 1); renderProps(); });
  p.querySelectorAll("[data-delacl]").forEach(b => b.onclick = () => { n.acl.splice(+b.dataset.delacl, 1); renderProps(); renderCanvas(); });
  if ($("add-sr")) $("add-sr").onclick = () => { n.static_routes.push({ dest: "", next_hop: "" }); renderProps(); };
  if ($("add-acl")) $("add-acl").onclick = () => { n.acl.push({ action: "deny", src: "any", dst: "any" }); renderProps(); renderCanvas(); };
  $("p-del").onclick = deleteSelected;
}

function linkProps(p) {
  const l = linkById(sel.id); if (!l) { sel = null; return renderProps(); }
  const a = nodeById(l.nodeA), b = nodeById(l.nodeB);
  p.innerHTML = `<div class="panel-title">Link</div><div class="mono" style="font-size:12px">${esc(a.hostname)}:${l.ifaceA} ⇄ ${esc(b.hostname)}:${l.ifaceB}</div>
    <label class="field-label">Cost (OSPF metric)</label><input class="field" id="l-cost" type="number" min="1" value="${l.cost}">
    <label class="field-label">Delay (ms, one way)</label><input class="field" id="l-delay" type="number" min="0.1" step="0.1" value="${l.delay}">
    <div class="hint">OSPF minimises total <b>cost</b>; RIP ignores cost and counts <b>hops</b>. Delay feeds ping/traceroute times.</div>
    <button class="btn-small" id="l-toggle">${l.up === false ? "Bring link UP" : "Take link DOWN (simulate failure)"}</button><br>
    <button class="btn-small danger" id="l-del">Delete link</button>`;
  $("l-cost").oninput = e => { l.cost = Math.max(1, +e.target.value || 1); renderCanvas(); };
  $("l-delay").oninput = e => { l.delay = Math.max(0.1, +e.target.value || 2); };
  $("l-toggle").onclick = () => { l.up = (l.up === false); log(`Link ${a.hostname} <-> ${b.hostname} is now ${l.up ? "UP" : "DOWN"}.`, l.up ? "ok" : "warn"); renderProps(); renderCanvas(); };
  $("l-del").onclick = deleteSelected;
}

// ---------- simulate selectors ---------------------------------------------
function renderSim() {
  const opts = [], hosts = [];
  for (const n of state.nodes) if (n.type !== "switch") for (const i of n.interfaces) {
    const ip = bareIp(i.ip); if (!validIp(ip)) continue;
    const o = [ip, `${n.hostname} · ${ip}`]; opts.push(o); if (n.type === "host") hosts.push(o);
  }
  const pool = hosts.length > 1 ? hosts : opts;            // prefer hosts as sensible defaults
  const html = opts.map(([v, t]) => `<option value="${v}">${esc(t)}</option>`).join("") || `<option value="">(no IPs yet)</option>`;
  const keep = id => { const prev = $(id).value; $(id).innerHTML = html; if (opts.some(o => o[0] === prev)) $(id).value = prev; return opts.some(o => o[0] === prev); };
  const a = keep("sim-src"), b = keep("sim-dst");
  if (!a && pool.length) $("sim-src").value = pool[0][0];
  if (!b && pool.length) $("sim-dst").value = pool[pool.length - 1][0];
  if (pool.length > 1 && $("sim-src").value === $("sim-dst").value) $("sim-dst").value = pool.find(o => o[0] !== $("sim-src").value)[0];
}

// ---------- animation -------------------------------------------------------
function animate(ids, cls) {
  return new Promise(done => {
    const pts = ids.map(nodeById).filter(Boolean); if (pts.length < 2) return done();
    const dot = el("circle", { r: 5, class: "pulse-dot " + cls }, $("pulse-layer"));
    let seg = 0, t0 = performance.now(); const ms = 380;
    const step = now => {
      const t = Math.min((now - t0) / ms, 1), a = pts[seg], b = pts[seg + 1];
      dot.setAttribute("cx", a.x + (b.x - a.x) * t); dot.setAttribute("cy", a.y + (b.y - a.y) * t);
      if (t < 1) requestAnimationFrame(step);
      else if (++seg < pts.length - 1) { t0 = performance.now(); requestAnimationFrame(step); }
      else { dot.remove(); done(); }
    };
    requestAnimationFrame(step);
  });
}

async function simulate(kind) {
  const src = $("sim-src").value, dst = $("sim-dst").value;
  if (!src || !dst) return log("Pick a source and a destination first.", "warn");
  if (src === dst) return log("Source and destination are the same device.", "warn");
  if (animating) return;
  $("pulse-layer").innerHTML = ""; overlay.failedAt = null; renderCanvas();
  try {
    const r = await api(kind === "ping" ? "/api/ping" : "/api/traceroute", { src, dst });
    log("", ""); logLines(r.log);
    animating = true;
    if (r.path && r.path.length > 1) { await animate(r.path, ""); if (r.success && r.return_path.length > 1) await animate(r.return_path, "back"); }
    animating = false;
    if (!r.success && r.failed_at) { overlay.failedAt = r.failed_at; renderCanvas(); }
  } catch (e) { animating = false; log("Simulation error: " + e.message, "err"); }
}
$("btn-ping").onclick = () => simulate("ping");
$("btn-trace").onclick = () => simulate("trace");

// ---------- validate --------------------------------------------------------
async function runValidate() {
  const r = await api("/api/validate");
  overlay.issueNodes = new Set(r.issues.flatMap(i => i.nodes)); overlay.issueLinks = new Set(r.issues.flatMap(i => i.links)); renderCanvas();
  log(`— validation: ${r.errors} error(s), ${r.warnings} warning(s) —`, "muted");
  if (!r.issues.length) log("✓ No issues. Every rule passed.", "ok");
  r.issues.forEach(i => { log(`[${i.severity.toUpperCase()}] ${i.rule}: ${i.message}`, i.severity === "error" ? "err" : "warn"); if (i.fix) log(`    fix: ${i.fix}`, "muted"); });
  const chip = $("status-chip"); chip.textContent = `health ${r.score}/100 · ${r.errors}E ${r.warnings}W`;
  chip.className = "status-chip " + (r.errors ? "err" : r.warnings ? "warn" : "ok");
}
$("btn-validate").onclick = () => runValidate().catch(e => log(e.message, "err"));

// ---------- routing tables --------------------------------------------------
$("protocol").onchange = e => { state.settings.protocol = e.target.value; log(`Routing protocol: ${e.target.value.toUpperCase()}.`, "ok"); };
$("btn-routing").onclick = async () => {
  const r = await api("/api/routing");
  if (!r.tables.length) return log("No routers in the topology.", "warn");
  let h = `<div class="banner info">Protocol: <b>${r.protocol.toUpperCase()}</b> · <b>C</b> connected, <b>S</b> static, <b>O</b> OSPF, <b>R</b> RIP · The router picks the longest prefix, then lowest AD, then lowest metric.</div>`;
  if (r.loops.length) h += `<div class="banner err">Routing loop: ${r.loops.map(l => esc(l.names.join(" → ")) + " for " + l.dest).join("; ")}</div>`;
  for (const t of r.tables) h += `<h4>${esc(t.hostname)}</h4><table class="tbl"><tr><th></th><th>Destination</th><th>AD / metric</th><th>Next hop</th><th>Via</th><th>Out</th></tr>${t.routes.map(x => `<tr><td class="k">${x.kind}</td><td>${x.dest}</td><td>${x.ad} / ${x.metric}</td><td>${x.next_hop || "—"}</td><td>${esc(x.via || "directly connected")}</td><td>${x.out_if}</td></tr>`).join("")}</table>`;
  modal("Routing tables", h);
};

$("btn-compare").onclick = async () => {
  const src = $("sim-src").value, dst = $("sim-dst").value; if (!src || !dst || src === dst) return log("Pick two different devices first.", "warn");
  const r = await api("/api/compare", { src, dst }), differ = r[0].names.join() !== r[1].names.join();
  let h = `<div class="banner info">${differ ? "<b>Different paths!</b> RIP counts hops and ignores link cost; OSPF adds up link costs and picks the cheapest route." : "Both protocols chose the same path for this pair."}</div><div class="cmp">`;
  for (const c of r) h += `<div class="cmp-card"><h4>${c.protocol.toUpperCase()}</h4>${c.ok ? `<div class="cmp-path">${esc(c.names.join(" → "))}</div><div class="muted">Routers crossed: ${c.routers} · total link cost: ${c.cost} · one-way time: ${c.time} ms</div>` : `<div class="banner err">${esc(c.msg)}</div>`}</div>`;
  modal(`Compare RIP vs OSPF  (${src} → ${dst})`, h + "</div>");
};

// ---------- matrix / subnet / configs --------------------------------------
$("btn-matrix").onclick = async () => {
  const r = await api("/api/matrix"); if (r.labels.length < 2) return log("Need at least two hosts with IPs for a matrix.", "warn");
  let h = `<div class="banner info">Every host pings every other host (request <b>and</b> reply must succeed). Red cells show where configuration, VLANs, ACLs or failed links break connectivity.</div><table class="tbl mx"><tr><th>from \\ to</th>${r.labels.map(l => `<th>${esc(l)}</th>`).join("")}</tr>`;
  r.matrix.forEach((row, i) => h += `<tr><th>${esc(r.labels[i])}</th>${row.map(v => v === null ? `<td class="s">—</td>` : v ? `<td class="y">✓</td>` : `<td class="n">✕</td>`).join("")}</tr>`);
  const total = r.matrix.flat().filter(v => v !== null), ok = total.filter(Boolean).length;
  modal("Reachability matrix", h + `</table><p class="muted">${ok} of ${total.length} host pairs can communicate.</p>`);
};

$("btn-subnet").onclick = () => {
  const b = modal("Subnet calculator", `<div class="row-actions" style="margin:0 0 12px"><input id="sc-in" class="field" value="192.168.10.37/26" style="max-width:260px"><button id="sc-go" class="btn btn-solid">Calculate</button></div><div id="sc-out"></div>`);
  const go = async () => {
    const res = await fetch("/api/subnet", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cidr: $("sc-in").value }) }), d = await res.json();
    $("sc-out").innerHTML = d.error ? `<div class="banner err">${esc(d.error)}</div>` : `<table class="tbl">${Object.entries(d).map(([k, v]) => `<tr><th style="width:170px">${k}</th><td>${esc(v)}</td></tr>`).join("")}</table>`;
  };
  $("sc-go").onclick = go; $("sc-in").onkeydown = e => { if (e.key === "Enter") go(); }; go();
};

$("btn-configs").onclick = async () => {
  const r = await api("/api/configs"); if (!state.nodes.length) return log("Nothing to export yet.", "warn");
  modal("Generated device configuration (Cisco IOS style)", `<pre class="cfg" id="cfg-text">${esc(r.combined)}</pre><div class="row-actions"><button id="cfg-dl" class="btn btn-solid">Download .txt</button><button id="cfg-cp" class="btn btn-outline">Copy</button></div>`);
  $("cfg-dl").onclick = () => download(`${slug(state.name)}-configs.txt`, r.combined, "text/plain");
  $("cfg-cp").onclick = () => navigator.clipboard.writeText(r.combined).then(() => log("Configs copied to clipboard.", "ok"));
};

// ---------- projects / templates / import-export ---------------------------
const slug = s => (s || "network").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "network";
function download(name, text, type) { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; a.click(); URL.revokeObjectURL(a.href); }

function setTopology(t, name) {
  state = { name: name || t.name || "Untitled project", nodes: t.nodes || [], links: t.links || [], settings: { protocol: (t.settings || {}).protocol || "ospf" } };
  for (const n of state.nodes) { n.interfaces ||= []; n.static_routes ||= []; n.acl ||= []; n.gateway ||= ""; n.x ??= 100 + Math.random() * 700; n.y ??= 100 + Math.random() * 400; }
  sel = null; mode = { place: null, link: false, first: null }; overlay = { issueNodes: new Set(), issueLinks: new Set(), failedAt: null };
  $("proj-name").value = state.name; $("protocol").value = state.settings.protocol; $("status-chip").textContent = "not validated"; $("status-chip").className = "status-chip";
  $("pulse-layer").innerHTML = ""; render();
}
$("proj-name").oninput = e => state.name = e.target.value;

$("tpl-select").onchange = async e => {
  const key = e.target.value; e.target.value = ""; if (!key) return;
  if (state.nodes.length && !confirm("Replace the current topology with this example?")) return;
  const t = await getJson("/api/templates/" + key); setTopology(t.topology, t.name);
  log(`Loaded "${t.name}" - ${t.description}`, "ok");
};

$("btn-new").onclick = () => { if (state.nodes.length && !confirm("Clear everything?")) return; setTopology({ nodes: [], links: [] }, "Untitled project"); log("New project.", "muted"); };
$("btn-export").onclick = () => download(slug(state.name) + ".json", JSON.stringify(state, null, 2), "application/json");
$("btn-import").onclick = () => $("import-file").click();
$("import-file").onchange = async e => {
  const f = e.target.files[0]; if (!f) return;
  try { const t = JSON.parse(await f.text()); setTopology(t, t.name || f.name.replace(/\.json$/, "")); log(`Imported ${f.name}.`, "ok"); } catch { log("That file is not a valid NetForge JSON export.", "err"); }
  e.target.value = "";
};

async function projectsModal() {
  const list = await getJson("/api/projects");
  const b = modal("Projects (saved in SQLite)", `<div class="row-actions" style="margin:0 0 12px"><input id="pj-name" class="field" style="max-width:260px" value="${esc(state.name)}"><button id="pj-save" class="btn btn-solid">Save current</button></div>
    ${list.length ? `<table class="tbl"><tr><th>Name</th><th>Devices</th><th>Links</th><th>Saved</th><th></th></tr>${list.map(p => `<tr><td>${esc(p.name)}</td><td>${p.nodes}</td><td>${p.links}</td><td class="muted">${new Date(p.updated * 1000).toLocaleString()}</td><td><button class="btn-small" data-load="${esc(p.name)}">Load</button> <button class="btn-small danger" data-del="${esc(p.name)}">Delete</button></td></tr>`).join("")}</table>` : `<div class="muted">No saved projects yet.</div>`}`);
  $("pj-save").onclick = async () => {
    const name = $("pj-name").value.trim(); if (!name) return;
    state.name = name; $("proj-name").value = name; await api("/api/projects", { name }); log(`Saved project "${name}".`, "ok"); projectsModal();
  };
  b.querySelectorAll("[data-load]").forEach(x => x.onclick = async () => { const d = await getJson("/api/projects/" + encodeURIComponent(x.dataset.load)); setTopology(d.topology, d.name); closeModal(); log(`Loaded project "${d.name}".`, "ok"); });
  b.querySelectorAll("[data-del]").forEach(x => x.onclick = async () => { if (confirm(`Delete "${x.dataset.del}"?`)) { await fetch("/api/projects/" + encodeURIComponent(x.dataset.del), { method: "DELETE" }); projectsModal(); } });
}
$("btn-projects").onclick = () => projectsModal().catch(e => log(e.message, "err"));
$("btn-clear-console").onclick = () => $("console-body").innerHTML = "";

// ---------- boot ------------------------------------------------------------
(async function boot() {
  try { (await getJson("/api/templates")).forEach(t => { const o = document.createElement("option"); o.value = t.key; o.textContent = t.name; $("tpl-select").appendChild(o); }); } catch { }
  log("NetForge ready. Open 'Example labs' to load a demo network, or build your own.", "muted");
  render();
})();

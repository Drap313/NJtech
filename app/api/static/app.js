/* Dispatch Guardian: multi-page operations console (hash routed, no external assets). */
"use strict";
const TZ = "America/New_York";
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fmtT = iso => iso ? new Date(iso).toLocaleTimeString("en-US",{timeZone:TZ,hour:"2-digit",minute:"2-digit",hour12:false}) : "–";
const fmtDT = iso => iso ? new Date(iso).toLocaleString("en-US",{timeZone:TZ,weekday:"short",hour:"2-digit",minute:"2-digit",hour12:false}) : "–";
const fmtD = iso => iso ? new Date(iso).toLocaleString("en-US",{timeZone:TZ,month:"short",day:"numeric",hour:"2-digit",minute:"2-digit",hour12:false}) : "–";
const hm = m => m==null ? "–" : (Math.abs(m)>=60 ? `${m<0?"-":""}${Math.floor(Math.abs(m)/60)}h ${String(Math.abs(m)%60).padStart(2,"0")}m` : `${m}m`);
const tag = (v, cls) => v ? `<span class="tag ${esc(cls||v)}">${esc(String(v).replace(/_/g," "))}</span>` : "";
const money = v => v==null ? "–" : (v<0?"-":"") + "$" + Math.abs(Math.round(v)).toLocaleString();
const short = s => String(s||"").replace(/ \(.*\)/, "");
const slackTag = s => s==null ? "–" : tag(hm(s), s<0?"LATE":s<15?"AT_RISK":"ON_TIME");

let S = null, MAP = null, nowMs = Date.now(), lastHtml = "", lastPage = "";
const cache = {}, inflight = {};
const CHAT = [];
let INTAKE = null, actFilter = {telemetry:false};

async function api(path, opts={}) {
  const r = await fetch(path, {headers: opts.body instanceof FormData ? {} : {"Content-Type":"application/json"}, ...opts});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || r.statusText);
  return j;
}
function toast(msg, ms=4500) { const t=$("#toast"); t.innerHTML=msg; t.style.display="block"; clearTimeout(t._h); t._h=setTimeout(()=>t.style.display="none", ms); }
function need(key, url, ttl=4000) {
  const c = cache[key];
  if ((!c || Date.now()-c.at > ttl) && !inflight[key]) {
    inflight[key] = api(url).then(d => { cache[key] = {data:d, at:Date.now()}; }).catch(e => { cache[key] = {data:{__error:e.message}, at:Date.now()}; })
      .finally(() => { delete inflight[key]; draw(); });
  }
  return c ? c.data : null;
}
const invalidate = prefix => Object.keys(cache).forEach(k => { if (k.startsWith(prefix)) delete cache[k]; });
const actor = () => $("#actor").value.trim();
function simNow() { if (!S) return new Date(); const c=S.clock, base=new Date(c.now).getTime(); return new Date(c.paused ? base : base + (Date.now()-nowMs)*c.speed); }

/* ---------------------------------------------------------------- icons & nav */
const I = p => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${p}</svg>`;
const ICON = {
  overview: I('<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>'),
  incidents: I('<path d="M12 3l9 16H3z"/><path d="M12 10v4M12 17h.01"/>'),
  loads: I('<rect x="2" y="7" width="13" height="10" rx="1"/><path d="M15 10h4l3 3v4h-7"/><circle cx="6" cy="18" r="1.6"/><circle cx="18" cy="18" r="1.6"/>'),
  drivers: I('<circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/>'),
  schedule: I('<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4M7 14h5M7 17h8"/>'),
  assistant: I('<path d="M4 5h16v11H8l-4 4z"/><path d="M8 10h8M8 13h5"/>'),
  activity: I('<path d="M3 12h4l3-8 4 16 3-8h4"/>'),
  policies: I('<path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z"/><path d="M9 12l2 2 4-4"/>'),
};
const NAV = [
  {sec:"Operate"}, {id:"overview", label:"Overview"}, {id:"incidents", label:"Incidents"}, {id:"loads", label:"Loads"},
  {id:"drivers", label:"Drivers"}, {id:"schedule", label:"Schedule"},
  {sec:"Govern"}, {id:"activity", label:"Activity & audit"}, {id:"policies", label:"Policies & models"},
];
function route() {
  const parts = (location.hash.replace(/^#\/?/, "") || "overview").split("/");
  return {page: parts[0] || "overview", id: parts[1] ? decodeURIComponent(parts[1]) : null};
}
function renderNav(r) {
  const open = S ? S.incidents.filter(i => i.status==="OPEN").length : 0;
  $("#nav").innerHTML = NAV.map(n => n.sec ? `<div class="sec">${n.sec}</div>` :
    `<a href="#/${n.id}" class="${r.page===n.id?"on":""}">${ICON[n.id]}<span>${n.label}</span>${n.id==="incidents"&&open?`<span class="badge">${open}</span>`:""}</a>`).join("");
}

/* ---------------------------------------------------------------- shared widgets */
/* ---- live map (Leaflet, real road geometry) --------------------------------------- */
const COLORS = {ok:"#2ea043", warn:"#d29922", bad:"#f85149", idle:"#8b98a8", done:"#58a6ff"};
let SEL = null, COLOR_BY = "status";
function asgStatus(a) {
  if (!a) return "idle";
  if (a.status === "COMPLETED") return "done";
  if (a.status !== "DISPATCHED") return "idle";
  if (a.verdict === "FAIL" || a.tier === "LATE") return "bad";
  if (a.verdict === "UNKNOWN" || a.verdict === "MANUAL_REVIEW" || a.tier === "AT_RISK") return "warn";
  return "ok";
}
function hosStatus(a) {
  if (!a || a.status !== "DISPATCHED") return "idle";
  if (a.reserve == null) return a.verdict === "UNKNOWN" ? "warn" : "idle";
  return a.reserve < 30 ? "bad" : a.reserve < 90 ? "warn" : "ok";
}
const colorOf = a => COLORS[COLOR_BY === "hos" ? hosStatus(a) : asgStatus(a)];
const routeLegs = rid => (MAP && MAP.routes[rid] && MAP.routes[rid].legs) || [];
function legPoint(pts, f) {
  if (pts.length < 2) return {pt: pts[0], idx: 1};
  const d = [0]; for (let i = 1; i < pts.length; i++) d.push(d[i-1] + Math.hypot(pts[i][0]-pts[i-1][0], (pts[i][1]-pts[i-1][1])*0.76));
  const target = f * d[d.length-1];
  for (let i = 1; i < pts.length; i++) if (d[i] >= target) {
    const t = (target - d[i-1]) / Math.max(1e-9, d[i]-d[i-1]);
    return {pt: [pts[i-1][0] + t*(pts[i][0]-pts[i-1][0]), pts[i-1][1] + t*(pts[i][1]-pts[i-1][1])], idx: i};
  }
  return {pt: pts[pts.length-1], idx: pts.length-1};
}
function splitRoute(rid, minutes) {
  const done = [], rest = []; let pos = null;
  for (const leg of routeLegs(rid)) {
    if (minutes >= leg.to_min) { done.push(leg.points); pos = leg.points[leg.points.length-1]; continue; }
    if (minutes <= leg.from_min) { rest.push(leg.points); continue; }
    const r = legPoint(leg.points, (minutes - leg.from_min) / (leg.to_min - leg.from_min));
    pos = r.pt; done.push([...leg.points.slice(0, r.idx), r.pt]); rest.push([r.pt, ...leg.points.slice(r.idx)]);
  }
  return {done, rest, pos};
}
function truckPos(a) {
  const wps = MAP && MAP.routes[a.route_id] ? MAP.routes[a.route_id].waypoints : null;
  if (!wps) return null;
  if (a.stage === "IN_TRANSIT") return splitRoute(a.route_id, a.route_done).pos || [wps[0].lat, wps[0].lon];
  if (a.stage === "AT_DELIVERY" || a.stage === "DELIVERED") { const w = wps[wps.length-1]; return [w.lat, w.lon]; }
  return [wps[0].lat, wps[0].lon];
}
function repositionPath(d, pointId) {
  const loc = id => MAP.locations.find(l => l.id === id), end = loc(pointId); if (!end) return null;
  for (const [key, pts] of Object.entries(MAP.pairs || {})) {
    const [a, b] = key.split("|"); if (a !== pointId && b !== pointId) continue;
    const start = loc(a === pointId ? b : a), line = a === pointId ? pts.slice().reverse() : pts;
    const dist = (p, q) => Math.hypot(p[0]-q[0], (p[1]-q[1])*0.76);
    const total = dist([start.lat, start.lon], [end.lat, end.lon]), fs = dist([start.lat, start.lon], [d.lat, d.lon]), te = dist([d.lat, d.lon], [end.lat, end.lon]);
    if (fs + te > total * 1.25) continue;
    const r = legPoint(line, Math.max(0, Math.min(1, fs / Math.max(1e-9, fs + te))));
    return {pos: r.pt, points: [r.pt, ...line.slice(r.idx)]};
  }
  return null;
}
const isLight = () => document.documentElement.dataset.theme === "light";
class FleetMap {
  constructor(el, opts = {}) {
    this.opts = opts; this.el = el;
    this.map = L.map(el, {zoomControl: true}).setView([40.4, -77.5], 7);
    this.setTiles();
    this.routes = L.layerGroup().addTo(this.map); this.places = L.layerGroup().addTo(this.map); this.trucks = L.layerGroup().addTo(this.map);
    this.fitted = null;
    this.map.on("click", () => { if (!opts.only && SEL) { SEL = null; draw(true); } });
  }
  setTiles() {
    // Keyless basemaps: Esri gray canvas (+labels); OpenStreetMap if Esri tiles fail.
    if (this.tiles) this.tiles.forEach(t => this.map.removeLayer(t));
    const shade = isLight() ? "Light" : "Dark", esri = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/";
    const base = L.tileLayer(`${esri}World_${shade}_Gray_Base/MapServer/tile/{z}/{y}/{x}`, {maxZoom: 16, attribution: "Tiles &copy; Esri, HERE, Garmin, &copy; OpenStreetMap"});
    const ref = L.tileLayer(`${esri}World_${shade}_Gray_Reference/MapServer/tile/{z}/{y}/{x}`, {maxZoom: 16, opacity: 0.9});
    let fell = false;
    base.on("tileerror", () => { if (fell) return; fell = true; this.map.removeLayer(base); this.tiles.push(L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {maxZoom: 18, attribution: "&copy; OpenStreetMap"}).addTo(this.map).bringToBack()); });
    this.tiles = [base.addTo(this.map), ref.addTo(this.map)];
    this.shade = shade;
  }
  draw() {
    if (!S || !MAP) return;
    if (this.shade !== (isLight() ? "Light" : "Dark")) this.setTiles();
    const only = this.opts.only, focus = only || SEL;
    this.routes.clearLayers(); this.places.clearLayers(); this.trucks.clearLayers();
    const shown = S.assignments.filter(a => only ? a.id === only : a.status === "DISPATCHED");
    const pick = id => { if (only) return; SEL = SEL === id ? null : id; draw(true); };
    for (const a of shown) {
      const col = colorOf(a), faded = focus && a.id !== focus, w = a.id === focus ? 6 : 4;
      const {done, rest} = a.stage === "IN_TRANSIT" ? splitRoute(a.route_id, a.route_done) : {done: [], rest: routeLegs(a.route_id).map(l => l.points)};
      rest.forEach(pts => L.polyline(pts, {color: col, weight: w, opacity: faded ? 0.12 : 0.9, lineCap: "round"}).addTo(this.routes).on("click", e => { L.DomEvent.stop(e); pick(a.id); }));
      done.forEach(pts => L.polyline(pts, {color: "#8b98a8", weight: w - 1, opacity: faded ? 0.08 : 0.6, dashArray: "1 7", lineCap: "round"}).addTo(this.routes));
      if (a.relay && !faded) this.place(a.relay.point_id, `Relay: ${a.relay.driver_id} takes over`, true, "#a371f7");
      if (a.id === focus) MAP.routes[a.route_id].waypoints.forEach((wp, i, arr) => { const end = i === 0 || i === arr.length - 1; this.place(wp.loc, (i === 0 ? "Pickup: " : end ? "Delivery: " : "") + short(wp.name), end, end ? "#e6edf3" : "#8b98a8"); });
    }
    if (!only) MAP.locations.filter(l => l.kind === "terminal").forEach(l => this.place(l.id, short(l.name), false, "#76b900"));
    const placed = new Set();
    for (const a of shown) { const pos = truckPos(a); if (!pos || !a.driver_id) continue; placed.add(a.driver_id); this.truck(pos, `${a.load_id} · ${a.driver_id}`, colorOf(a), focus && a.id !== focus, () => pick(a.id)); }
    for (const d of S.drivers) {
      if (placed.has(d.id) || d.lon < -83.8) continue;
      const a = S.assignments.find(x => x.relay && x.relay.driver_id === d.id && x.status === "DISPATCHED");
      if (only && (!a || a.id !== only)) continue;
      let pos = [d.lat, d.lon];
      if (a) { const path = repositionPath(d, a.relay.point_id); if (path) { if (!(focus && a.id !== focus)) L.polyline(path.points, {color: colorOf(a), weight: 3, opacity: 0.75, dashArray: "6 6"}).addTo(this.routes); pos = path.pos; } }
      this.truck(pos, a ? `${d.id} → relay` : d.id, a ? colorOf(a) : COLORS.idle, focus && (!a || a.id !== focus), () => a ? pick(a.id) : (location.hash = `#/drivers/${d.id}`), !a);
    }
    const target = only || SEL || "ALL";
    if (this.fitted !== target) {
      const pts = []; (focus ? shown.filter(a => a.id === focus) : shown).forEach(a => routeLegs(a.route_id).forEach(l => l.points.forEach(q => pts.push(q))));
      if (pts.length) this.map.fitBounds(L.latLngBounds(pts).pad(0.08), {animate: true, maxZoom: 10});
      this.fitted = target;
    }
  }
  place(locId, label, permanent, color) {
    const l = MAP.locations.find(x => x.id === locId); if (!l) return;
    L.circleMarker([l.lat, l.lon], {radius: 5, color: "#fff", weight: 2, fillColor: color, fillOpacity: 1}).addTo(this.places).bindTooltip(esc(label), {permanent, direction: "right", className: "place", offset: [6, 0]});
  }
  truck(pos, label, color, faded, onClick, idle) {
    const icon = L.divIcon({className: `truck${faded ? " faded" : ""}${idle ? " idle" : ""}`, html: `<span style="background:${color}">${esc(label)}</span>`, iconSize: [0, 0]});
    L.marker(pos, {icon, zIndexOffset: faded ? 0 : 1000}).addTo(this.trucks).on("click", e => { L.DomEvent.stop(e); onClick(); });
  }
}
// Maps live outside the re-rendered HTML: each slot gets a persistent Leaflet instance moved back in after every render.
const LMAPS = {};
function lslot(key, height, opts = {}) { LMAPS[key] = LMAPS[key] || {opts, height}; LMAPS[key].opts = opts; return `<div class="lslot" data-lslot="${key}" style="height:${height}px"></div>`; }
function mountSlots() {
  document.querySelectorAll("[data-lslot]").forEach(slot => {
    const k = slot.dataset.lslot, m = LMAPS[k];
    if (!m.el) { m.el = document.createElement("div"); m.el.className = "lmap"; slot.appendChild(m.el); m.fm = new FleetMap(m.el, m.opts); }
    else if (m.el.parentNode !== slot) slot.appendChild(m.el);
    if (k === "overview") slot.insertAdjacentHTML("beforeend", `<div class="maplegend">${legendHTML()}</div>`);
    m.fm.opts = m.opts; m.fm.map.invalidateSize(); m.fm.draw();
  });
}
function legendHTML() {
  const items = COLOR_BY === "hos" ? [["ok","90+ min legal time left"],["warn","30–90 min"],["bad","under 30 min"],["idle","idle driver"]]
    : [["ok","on track"],["warn","at risk / data stale"],["bad","plan broken"],["idle","idle driver"]];
  return `<div class="seg2"><button data-colorby="status" class="${COLOR_BY==="status"?"on":""}">Status</button><button data-colorby="hos" class="${COLOR_BY==="hos"?"on":""}">Legal hours left</button></div>${items.map(([k,t]) => `<div><i style="background:${COLORS[k]}"></i>${t}</div>`).join("")}`;
}
function selectedLoad(id) {
  const a = S.assignments.find(x => x.id === id); if (!a) return "";
  const inc = S.incidents.find(i => i.status === "OPEN" && i.assignment_id === id);
  return `<div class="row" style="padding-top:10px;gap:16px"><b>${a.load_id}</b> ${esc(short(a.origin))} → ${esc(short(a.destination))} ${tag(a.verdict)}
    <span class="muted">driver ${a.relay ? `${a.driver_id} → ${a.relay.driver_id}` : esc(a.driver_id || "–")} · arrives ${fmtT(a.delivery_eta)} · ${slackTag(a.slack)} · legal time left ${hm(a.reserve)}</span>
    <a class="btn sm" href="#/loads/${a.id}">Open load</a>${inc ? `<a class="btn sm primary" href="#/incidents/${inc.id}">Resolve</a>` : ""}</div>`;
}
/* ---- schedule board ---- */
function board(d, only) {
  const rows = only ? d.drivers.filter(r => r.id === only) : d.drivers;
  const now = +simNow();
  let t0 = now - 3*3600e3, t1 = now + 15*3600e3;
  rows.forEach(r => r.blocks.forEach(b => { t0 = Math.min(t0, +new Date(b.start)); t1 = Math.max(t1, +new Date(b.end)); }));
  t0 = Math.max(t0, now - 6*3600e3); t1 = Math.min(t1, now + 26*3600e3);
  const W = 1400, Lp = 210, R = 12, rowH = 38, top = 26, H = top + rows.length*rowH + 6, x = t => Lp + (Math.max(t0, Math.min(t1, t)) - t0) / (t1 - t0) * (W - Lp - R);
  const ink = "fill:var(--text)", dim = "fill:var(--dim)";
  let h = "";
  for (let t = Math.ceil(t0/3600e3)*3600e3; t <= t1; t += 3600e3) {
    const major = +new Date(t).toLocaleString("en-US", {timeZone: TZ, hour: "numeric", hour12: false}) % 3 === 0;
    h += `<line x1="${x(t)}" x2="${x(t)}" y1="${top-4}" y2="${H}" style="stroke:var(--line2)" stroke-opacity="${major ? 1 : .5}"/>${major ? `<text x="${x(t)}" y="15" style="${dim}" font-size="11" text-anchor="middle">${fmtT(new Date(t).toISOString())}</text>` : ""}`;
  }
  (d.windows || []).forEach(w => {
    const r = rows.findIndex(row => row.blocks.some(b => b.stop === "delivery" && b.load_id === w.load_id)); if (r < 0) return;
    const y = top + r*rowH;
    h += `<rect x="${x(+new Date(w.start))}" y="${y+2}" width="${Math.max(2, x(+new Date(w.end)) - x(+new Date(w.start)))}" height="${rowH-4}" fill="#2ea043" fill-opacity=".1" stroke="#2ea043" stroke-opacity=".45" stroke-dasharray="3 3"><title>${esc(w.load_id)} delivery window ${fmtT(w.start)}–${fmtT(w.end)}</title></rect>`;
  });
  rows.forEach((r, i) => {
    const y = top + i*rowH;
    const st = r.stale ? COLORS.warn : r.blocks.some(b => b.verdict === "FAIL") ? COLORS.bad : r.blocks.length ? COLORS.ok : COLORS.idle;
    h += `<a href="#/drivers/${r.id}"><circle cx="10" cy="${y+rowH/2}" r="4" fill="${st}"/><text x="20" y="${y+16}" style="${ink}" font-size="12.5" font-weight="600">${esc(r.id)} ${esc(r.name)}</text>
      <text x="20" y="${y+30}" style="${dim}" font-size="11">${esc(r.loads.join(", ") || r.availability)} · ${hm(r.drive_left)} driving left</text></a>`;
    if (!r.blocks.length) h += `<text x="${Lp+6}" y="${y+23}" style="${dim}" font-size="11.5">${r.stale ? "Logbook stale: schedule unknown" : r.availability === "available" ? "Available" : esc(r.duty_status.replace("_", " ").toLowerCase())}</text>`;
    r.blocks.forEach(b => {
      const a = x(+new Date(b.start)), e = x(+new Date(b.end)); if (e - a < 0.5) return;
      const col = b.stop === "break" ? "#a371f7" : b.stop === "reset" ? "#6e7681" : b.stop === "handoff" ? "#2ea043" : b.status === "DRIVING" ? "#2f81f7" : b.status === "ON_DUTY" ? "#d29922" : "#3a4553";
      h += `<rect x="${a}" y="${y+8}" width="${Math.max(1.5, e-a)}" height="${rowH-16}" rx="4" fill="${col}" fill-opacity="${b.status === "OFF_DUTY" && !["break","reset"].includes(b.stop) ? .45 : .95}"><title>${esc(r.id)} · ${esc(b.load_id)} · ${esc(b.label)} · ${fmtT(b.start)}–${fmtT(b.end)}</title></rect>`;
      if (e - a > 70 && b.status !== "OFF_DUTY") h += `<text x="${a+5}" y="${y+23}" fill="#fff" font-size="10.5" font-weight="600" pointer-events="none">${esc(b.label.slice(0, Math.floor((e-a)/6.2)))}</text>`;
    });
  });
  h += `<line x1="${x(now)}" x2="${x(now)}" y1="${top-8}" y2="${H}" stroke="#f85149" stroke-width="1.5"/><text x="${x(now)+4}" y="${top-10}" fill="#f85149" font-size="10.5">now</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block">${h}</svg>
    <div class="legend" style="margin-top:10px"><span><i style="background:#2f81f7"></i>driving</span><span><i style="background:#d29922"></i>on duty</span><span><i style="background:#a371f7"></i>30-min break</span><span><i style="background:#6e7681"></i>10-h rest</span><span><i style="background:#2ea043"></i>handoff</span><span><i style="background:#3a4553"></i>off duty</span></div>`;
}
function schedRow(id) { const d = need("schedule", "/api/schedule", 3000); return d && !d.__error ? board(d, id) : loading; }

function gantt(timeline, opts={}) {
  if (!timeline || !timeline.length) return `<div class="empty">No timeline.</div>`;
  const rows = []; const seen = {};
  timeline.forEach(e => { const k = e.driver_id+"|"+e.role; if (!(k in seen)) { seen[k]=rows.length; rows.push({k, d:e.driver_id, role:e.role, items:[]}); } rows[seen[k]].items.push(e); });
  let t0 = Math.min(...timeline.map(e => +new Date(e.start))), t1 = Math.max(...timeline.map(e => +new Date(e.end)));
  if (opts.window) { t1 = Math.max(t1, +new Date(opts.window.end) + 30*60e3); }
  const now = +simNow(); t0 = Math.min(t0, now);
  const W = 1000, L = 110, R = 12, rowH = 30, top = 22, H = top + rows.length*rowH + 8;
  const x = t => L + (t - t0) / (t1 - t0) * (W - L - R);
  let h = "";
  const step = (t1-t0) > 20*3600e3 ? 4*3600e3 : (t1-t0) > 8*3600e3 ? 2*3600e3 : 3600e3;
  for (let t = Math.ceil(t0/step)*step; t <= t1; t += step) {
    h += `<line x1="${x(t)}" x2="${x(t)}" y1="${top-4}" y2="${H-6}" stroke="#1c2531"/><text x="${x(t)}" y="13" fill="#5f6b7a" font-size="10.5" text-anchor="middle">${fmtT(new Date(t).toISOString())}</text>`;
  }
  if (opts.window) {
    const a = x(+new Date(opts.window.start)), b = x(+new Date(opts.window.end));
    h += `<rect x="${a}" y="${top-4}" width="${Math.max(2,b-a)}" height="${H-top-2}" fill="#3fb950" fill-opacity=".08" stroke="#3fb950" stroke-opacity=".35" stroke-dasharray="3 3"/>
      <text x="${b-4}" y="${H-10}" fill="#56d364" font-size="10" text-anchor="end">delivery window</text>`;
  }
  rows.forEach((r, i) => {
    const y = top + i*rowH;
    h += `<text x="4" y="${y+18}" fill="#c9d1d9" font-size="11.5" font-family="ui-monospace,monospace">${esc(r.d)}</text><text x="48" y="${y+18}" fill="#5f6b7a" font-size="10.5">${esc(r.role)}</text>`;
    r.items.forEach(e => {
      if (e.stop === "status") return;
      const a = x(+new Date(e.start)), b = x(+new Date(e.end));
      const col = e.stop==="break" ? "#a371f7" : e.stop==="reset" ? "#6e7681" : e.stop==="handoff" ? "#3fb950" : e.status==="DRIVING" ? "#2f81f7" : e.status==="ON_DUTY" ? "#d29922" : "#2a3442";
      h += `<rect x="${a}" y="${y+5}" width="${Math.max(1.5,b-a)}" height="${rowH-12}" rx="3" fill="${col}" fill-opacity="${e.status==="OFF_DUTY"&&e.stop!=="break"&&e.stop!=="reset"?.55:.9}"><title>${esc(e.label)} · ${fmtT(e.start)}–${fmtT(e.end)} (${e.minutes}m)</title></rect>`;
      if (b - a > 70) h += `<text x="${a+5}" y="${y+19}" fill="#0b0f15" font-size="10.5" font-weight="600" pointer-events="none">${esc(e.label.slice(0, Math.floor((b-a)/6.2)))}</text>`;
    });
  });
  if (now >= t0 && now <= t1) h += `<line x1="${x(now)}" x2="${x(now)}" y1="${top-6}" y2="${H-4}" stroke="#ff7b72" stroke-width="1.5"/><text x="${x(now)+3}" y="${top-8}" fill="#ff7b72" font-size="10">now</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:${H}px;display:block">${h}</svg>
    <div class="legend" style="margin-top:6px"><span><i style="background:#2f81f7"></i>driving</span><span><i style="background:#d29922"></i>on duty</span><span><i style="background:#a371f7"></i>30-min break</span><span><i style="background:#6e7681"></i>10-h reset</span><span><i style="background:#3fb950"></i>handoff</span><span><i style="background:#2a3442"></i>off duty</span></div>`;
}
function clockGauge(label, v, lim) {
  const pct = Math.max(0, Math.min(100, 100*v/lim)), col = v < 30 ? "var(--bad)" : v < 90 ? "var(--warn)" : "var(--ok)";
  return `<div class="gauge"><span class="muted">${label}</span><div class="bar"><i style="width:${pct}%;background:${col}"></i></div><span class="mono">${hm(v)}</span></div>`;
}
function clockCell(v, lim) {
  const pct = Math.max(0, Math.min(100, 100*v/lim)), col = v < 30 ? "var(--bad)" : v < 90 ? "var(--warn)" : "var(--ok)";
  return `<span class="mono">${hm(v)}</span><div class="bar" style="margin-top:3px"><i style="width:${pct}%;background:${col}"></i></div>`;
}
const card = (title, body, right="", flush=false) => `<div class="card"><div class="hd"><h3>${title}</h3>${right?`<span class="r">${right}</span>`:""}</div><div class="bd ${flush?"flush":""}">${body}</div></div>`;
const loading = `<div class="empty">Loading…</div>`;
const errBox = d => d && d.__error ? `<div class="callout">${esc(d.__error)}</div>` : "";

/* ---------------------------------------------------------------- tables */
function loadsTable(list) {
  if (!list.length) return `<div class="empty">No loads.</div>`;
  return `<table><tr><th>Load</th><th>Status</th><th>Lane</th><th>Driver</th><th>Progress</th><th>Compliance</th><th>Delivery ETA</th><th>Slack</th><th>Reserve</th><th>Risk</th></tr>
  ${list.map(a => {
    const prog = a.stage==="IN_TRANSIT" ? Math.round(100*a.route_done/a.route_total) : ["DELIVERED","AT_DELIVERY"].includes(a.stage) ? 100 : 0;
    return `<tr class="click" data-href="#/loads/${a.id}">
      <td class="mono nowrap"><b>${a.load_id}</b><div class="dim">${a.id}</div></td>
      <td>${tag(a.status)}<div class="dim small">${a.priority} · ${a.equipment.replace("_"," ")}</div></td>
      <td>${esc(short(a.origin))} → ${esc(short(a.destination))}<div class="dim small">${esc(a.customer)}${a.pickup_delay?` · <span style="color:#e3b341">pickup +${a.pickup_delay}m</span>`:""}</div></td>
      <td class="mono nowrap">${a.relay?`${a.driver_id} → ${a.relay.driver_id}`:(a.driver_id||"–")}<div class="dim">${a.tractor_id||""} ${a.trailer_id||""}</div></td>
      <td class="nowrap">${a.stage.replace(/_/g," ").toLowerCase()}<div class="bar" style="margin-top:4px"><i style="width:${prog}%"></i></div></td>
      <td>${tag(a.verdict)}${a.failures.length?`<div class="dim mono small">${a.failures.join(", ")}</div>`:""}</td>
      <td class="mono nowrap">${fmtDT(a.delivery_eta)}<div class="dim">win ${fmtT(a.delivery_window.start)}–${fmtT(a.delivery_window.end)}</div></td>
      <td class="mono">${slackTag(a.slack)}</td><td class="mono">${hm(a.reserve)}</td><td>${a.risk==null?"–":tag(String(a.risk), a.risk_band)}</td></tr>`;
  }).join("")}</table>`;
}
function driversTable(list) {
  return `<table><tr><th>Driver</th><th>Availability</th><th>Duty</th><th>Location</th><th>Drive left</th><th>Window left</th><th>Cycle left</th><th>ELD</th><th>Assignment</th></tr>
  ${list.map(d => `<tr class="click" data-href="#/drivers/${d.id}">
    <td><span class="mono"><b>${d.id}</b></span> ${esc(d.name)}<div class="dim small">${d.home} · ${d.endorsements.join(",")||"no endorsements"}</div></td>
    <td>${tag(d.availability)}</td><td class="mono nowrap">${d.duty_status.replace("_"," ")}<div class="dim">since ${fmtT(d.status_since)}</div></td>
    <td>${esc(short(d.location))}</td><td>${clockCell(d.drive_left,660)}</td><td>${clockCell(d.window_left,840)}</td><td>${clockCell(d.cycle_left,4200)}</td>
    <td class="mono nowrap">${d.stale?tag(`${d.eld_age_min}m stale`,"UNKNOWN"):`${d.eld_age_min}m`}${d.feed!=="ok"?`<div class="dim">feed ${d.feed}</div>`:""}</td>
    <td class="mono">${d.assignment_id?`<a href="#/loads/${d.assignment_id}">${d.assignment_id}</a>`:"–"}</td></tr>`).join("")}</table>`;
}
function incidentRows(list) {
  if (!list.length) return `<div class="empty">No incidents. The monitor is watching every active load.</div>`;
  return list.map(i => {
    const rec = i.plans && i.plans[0];
    return `<div class="inc-row" data-href="#/incidents/${i.id}">
      <div style="min-width:86px">${tag(i.severity)}<div class="mono dim small" style="margin-top:4px">${i.id}</div></div>
      <div class="grow"><div class="t">${esc(i.title)}</div><div class="muted small">${esc(i.cause)}</div>
      ${rec && i.status==="OPEN" ? `<div class="small" style="margin-top:4px">Recommended: <b>${esc(rec.title)}</b> · ${slackTag(rec.appointment_slack_minutes)} · ${money(rec.incremental_cost)}</div>` : ""}</div>
      <div style="text-align:right">${tag(i.status)}<div class="dim small" style="margin-top:4px">${fmtT(i.updated_at)} · rev ${i.revision}</div></div></div>`;
  }).join("");
}

/* ---------------------------------------------------------------- pages */
const PAGES = {};

PAGES.overview = {
  crumbs: () => "Overview",
  render() {
    const act = S.assignments.filter(a => a.status==="DISPATCHED");
    const onTime = act.filter(a => a.verdict==="PASS" && a.slack!=null && a.slack>=0).length;
    const open = S.incidents.filter(i => i.status==="OPEN");
    const sev = open.filter(i => ["HIGH","CRITICAL"].includes(i.severity)).length;
    const avail = S.drivers.filter(d => d.availability==="available").length;
    const stale = S.drivers.filter(d => d.stale && d.assignment_id).length;
    const upcoming = act.filter(a => a.delivery_eta && new Date(a.delivery_eta) > simNow()).sort((a,b)=>new Date(a.delivery_eta)-new Date(b.delivery_eta))[0];
    const risky = S.assignments.filter(a => ["DISPATCHED","BLOCKED","DECLINED"].includes(a.status) && (a.verdict!=="PASS" || (a.slack!=null && a.slack<30) || a.status!=="DISPATCHED"));
    return `
    <div class="kpis">
      <div class="kpi"><div class="l">Active loads</div><div class="v">${act.length}</div><div class="s">${S.assignments.filter(a=>["PROPOSED","BLOCKED"].includes(a.status)).length} proposed for release</div></div>
      <div class="kpi ${onTime<act.length?"alert":""}"><div class="l">Legal &amp; on time</div><div class="v">${onTime}/${act.length}</div><div class="s">projected now</div></div>
      <div class="kpi ${sev?"alert":""}"><div class="l">Open incidents</div><div class="v">${open.length}</div><div class="s">${sev} high or critical</div></div>
      <div class="kpi"><div class="l">Drivers available</div><div class="v">${avail}</div><div class="s">of ${S.drivers.length}</div></div>
      <div class="kpi ${stale?"alert":""}"><div class="l">Stale ELD feeds</div><div class="v">${stale}</div><div class="s">on active loads</div></div>
      <div class="kpi"><div class="l">Next delivery</div><div class="v" style="font-size:18px">${upcoming?fmtT(upcoming.delivery_eta):"–"}</div><div class="s">${upcoming?`${upcoming.load_id} · ${short(upcoming.destination)}`:""}</div></div>
    </div>
    <div class="grid g-main">
      <div class="grid">
        ${card("Needs attention", incidentRows(open), `<a href="#/incidents">All incidents →</a>`, true)}
        ${card("Live fleet", lslot("overview", 520) + (SEL ? selectedLoad(SEL) : `<div class="muted small" style="padding-top:8px">Click a truck or route to focus it. Lines follow the real roads; dotted = already driven.</div>`), SEL ? `<a href="javascript:void 0" data-select="${SEL}">Show all loads</a>` : "")}
      </div>
      <div class="grid" style="align-content:start">
        ${card("Simulate an event", `<div class="muted small" style="margin-bottom:8px">Inject a scripted disruption. The monitor reevaluates within the same request.</div>
          ${(cache.scen?.data||[]).map(s=>`<button class="btn" style="margin:0 6px 6px 0" data-scenario="${s.name}">${esc(s.title)}</button>`).join("")}`)}
        ${card("Loads needing a look", risky.length ? risky.map(a => `<div class="inc-row" data-href="#/loads/${a.id}"><div class="mono" style="min-width:60px"><b>${a.load_id}</b></div>
          <div class="grow">${esc(short(a.origin))} → ${esc(short(a.destination))}<div class="dim small">${a.failures.join(", ")||"low slack"}</div></div>
          <div style="text-align:right">${tag(a.status!=="DISPATCHED"?a.status:a.verdict)}<div class="small" style="margin-top:3px">${slackTag(a.slack)}</div></div></div>`).join("") : `<div class="empty">Everything is legal with slack.</div>`, "", true)}
        ${card("Latest activity", S.events.slice(0,6).map(evItem).join("") || `<div class="empty">No events yet.</div>`, `<a href="#/activity">All →</a>`, true)}
      </div>
    </div>`;
  },
};

PAGES.incidents = {
  crumbs: r => r.id ? `<a href="#/incidents">Incidents</a><span class="sep">/</span>${esc(r.id)}` : "Incidents",
  render(r) { return r.id ? incidentDetail(r.id) : incidentList(); },
};
let incFilter = "OPEN";
function incidentList() {
  const list = S.incidents.filter(i => incFilter==="ALL" || (incFilter==="OPEN" ? i.status==="OPEN" : i.status!=="OPEN"));
  return `<div class="page-head"><div class="grow"><h1>Incidents</h1><div class="sub">Opened automatically when a plan becomes illegal, infeasible or fragile. Assignment changes need your approval.</div></div></div>
    <div class="tabs">${["OPEN","CLOSED","ALL"].map(f=>`<a href="javascript:void 0" data-incfilter="${f}" class="${incFilter===f?"on":""}">${f[0]+f.slice(1).toLowerCase()}</a>`).join("")}</div>
    <div class="card"><div class="bd flush">${incidentRows(list)}</div></div>`;
}
function compareTable(inc) {
  const ps = inc.plans; if (!ps.length) return "";
  const best = (f, lower=true) => { const v = ps.map(f).filter(x => x!=null); const b = lower ? Math.min(...v) : Math.max(...v); return p => f(p)===b; };
  const bestCost = best(p=>p.incremental_cost), bestSlack = best(p=>p.appointment_slack_minutes,false), bestRes = best(p=>p.hos_reserve_minutes,false), bestRisk = best(p=>p.risk_score);
  const row = (lab, f, isBest) => `<tr><td class="lab">${lab}</td>${ps.map(p=>`<td class="${isBest&&isBest(p)?"best":""}">${f(p)}</td>`).join("")}</tr>`;
  const open = inc.status==="OPEN";
  return `<table class="compare"><tr><th></th>${ps.map((p,i)=>`<th>${i===0&&open?tag("RECOMMENDED","PASS")+" ":""}${esc(p.kind_label||p.kind)}</th>`).join("")}</tr>
    ${row("Plan", p=>`<b>${esc(p.title)}</b>`)}
    ${row("Compliance", p=>`${tag(p.compliance_verdict)} <span class="dim small">re-check ${p.recheck?.verdict||"–"}</span>`)}
    ${row("Service", p=>tag(p.service_tier))}
    ${row("Delivery", p=>`<span class="mono">${fmtDT(p.delivery_eta)}</span>`)}
    ${row("Appointment slack", p=>slackTag(p.appointment_slack_minutes), bestSlack)}
    ${row("Legal reserve", p=>`<span class="mono">${hm(p.hos_reserve_minutes)}</span>`, bestRes)}
    ${row("Incremental cost", p=>`<span class="mono">${money(p.incremental_cost)}</span>`, bestCost)}
    ${row("Schedule risk", p=>`${tag(String(p.risk_score), p.risk_band)} <span class="dim small">${p.risk_band}</span>`, bestRisk)}
    ${row("Expires", p=>{const m=Math.round((new Date(p.expires_at)-simNow())/60000); return `<span class="mono">${m>0?`${m} min`:"expired"}</span> <span class="dim small">${esc(p.expiry_reason||"")}</span>`;})}
    ${open ? `<tr><td></td>${ps.map(p=>`<td>${p.rejected_by?tag("REJECTED","FAIL"):`<button class="btn primary sm" data-approve="${p.plan_id}">Approve &amp; execute</button> <button class="btn ghost sm" data-reject="${p.plan_id}">Reject</button>`}</td>`).join("")}</tr>`:""}
  </table>`;
}
function planDetail(p, inc, i) {
  const done = inc.executed_plan_id === p.plan_id;
  return `<div class="plan ${i===0&&inc.status==="OPEN"?"rec":""}">
    <div class="h">${done?tag("EXECUTED"):""}<b>${esc(p.title)}</b><span class="dim mono small">${p.plan_id}</span><span style="margin-left:auto">${tag(p.compliance_verdict)} ${tag(p.service_tier)}</span></div>
    <div class="mini"><div><div class="l">Delivery</div><div class="v">${fmtT(p.delivery_eta)}</div></div><div><div class="l">Slack</div><div class="v">${hm(p.appointment_slack_minutes)}</div></div>
      <div><div class="l">Legal reserve</div><div class="v">${hm(p.hos_reserve_minutes)}</div></div><div><div class="l">Incremental cost</div><div class="v">${money(p.incremental_cost)}</div></div>
      <div><div class="l">Schedule risk</div><div class="v">${p.risk_score}</div></div></div>
    ${gantt(p.timeline, {window: inc.evaluation?.delivery_window})}
    <details data-k="plan-${p.plan_id}"><summary>Cost breakdown, assumptions and rule trace</summary>
      <table style="margin-top:8px">${p.cost_breakdown.map(c=>`<tr><td>${esc(c.component)}</td><td class="mono">${money(c.amount)}</td><td>${tag(c.basis, c.basis)}</td><td class="dim">${esc(c.detail)}</td></tr>`).join("") || `<tr><td class="dim">No incremental cost.</td></tr>`}</table>
      <h3 style="margin-top:12px">Assumptions</h3><ul style="margin:0 0 0 16px;padding:0">${p.assumptions.map(a=>`<li class="muted">${esc(a)}</li>`).join("")||"<li class='dim'>none</li>"}</ul>
      <h3 style="margin-top:12px">Rule trace</h3><table>${(p.rule_trace||[]).map(t=>`<tr><td class="mono small">${esc(t.subject)}</td><td class="mono small">${esc(t.rule)}</td><td>${tag(t.status)}</td><td class="small">${esc(t.detail)}<div class="dim">${esc(t.citation)}</div></td></tr>`).join("")}</table>
    </details></div>`;
}
function incidentDetail(id) {
  const inc = need("inc:"+id, `/api/incidents/${encodeURIComponent(id)}`, 3000);
  if (!inc) return loading;
  if (inc.__error) return errBox(inc);
  const ev = inc.evaluation || {};
  const fails = (ev.hard_failures||[]).concat(ev.unknowns||[]);
  const explaining = S.explaining.includes(id), ex = inc.explanation;
  const trig = (S.events.concat(cache["events"]?.data||[])).filter(e => (inc.trigger_events||[]).includes(e.event_id));
  const trace = cache["trace:"+id]?.data;
  return `<div class="page-head"><div class="grow">
      <div class="row">${tag(inc.severity)} ${tag(inc.status)} <span class="mono dim">${inc.id} · rev ${inc.revision}</span></div>
      <h1 style="margin-top:6px">${esc(inc.title)}</h1>
      <div class="sub">Load <a href="#/loads/${inc.assignment_id}">${inc.load_id}</a> · opened ${fmtD(inc.created_at)} · updated ${fmtD(inc.updated_at)}${inc.suppressed_count?` · ${inc.suppressed_count} duplicate alerts suppressed`:""}</div></div>
      <div class="row"><button class="btn" data-explain="${id}" ${explaining?"disabled":""}>${explaining?"Local model thinking…":"Explain with local model"}</button>
      ${inc.status==="OPEN" && !(ev.hard_failures||[]).length ? `<button class="btn" data-override="${id}">Record override</button>`:""}</div></div>
    <div class="grid g-main">
      <div class="grid" style="align-content:start">
        ${card("What broke", `
          <div><span class="muted">Cause:</span> ${esc(inc.cause)}</div>
          <div style="margin-top:4px"><span class="muted">Customer impact:</span> ${esc(inc.customer_impact)}</div>
          ${fails.map(f=>`<div class="callout"><b class="mono">${esc(f.code)}${f.excess_minutes?` +${f.excess_minutes} min`:""}</b> · ${esc(f.detail)}<div class="dim small">${esc(f.citation||"")}</div></div>`).join("")}
          ${trig.length?`<div class="muted small" style="margin-top:6px">Triggered by ${trig.map(e=>`<span class="mono">${e.type}</span> from ${esc(e.source)} at ${fmtT(e.occurred_at)}`).join("; ")}</div>`:""}
          ${(inc.auto_actions||[]).map(a=>`<div class="callout info small">Automatic: ${esc(a.what)}</div>`).join("")}
          <h3 style="margin-top:14px">Current plan${inc.current_plan?.title?`: ${esc(inc.current_plan.title)}`:""}</h3>${gantt(ev.timeline, {window: ev.delivery_window})}`)}
        ${inc.plans.length ? card("Options compared", compareTable(inc), "best value per row highlighted", true) : ""}
        ${inc.plans.map((p,i) => planDetail(p, inc, i)).join("")}
        ${inc.rejected?.length ? card(`Excluded by hard constraints (${inc.rejected.length})`, `<table><tr><th>Option</th><th>Codes</th><th>Detail</th></tr>${inc.rejected.map(r=>`<tr><td>${esc(r.title)}</td><td class="mono small">${r.codes.join(", ")}</td><td class="small muted">${esc(r.detail||"")}</td></tr>`).join("")}</table>`, "never shown as options", true) : ""}
      </div>
      <div class="grid" style="align-content:start">
        ${card("Local model explanation", ex ? `<div class="msg bot" style="margin:0">${esc(ex.text)}</div><div class="steps">${esc(ex.model)} · ${ex.elapsed_s}s · tools: ${ex.steps.map(s=>s.tool).join(", ")||"none"}</div>` :
          `<div class="muted">${explaining?"Qwen3.6 is reading the incident and tool outputs…":"Ask the local planner model to explain the incident and the recommendation. It can read tools but cannot change anything."}</div>`)}
        ${card("Dispatcher message", `<div class="summary">${esc(inc.summary)}</div>`, "as queued to the dispatch channel")}
        ${card("History", (inc.history||[]).slice().reverse().map(h=>`<div class="small" style="padding:4px 0;border-bottom:1px solid var(--line2)"><span class="mono dim">${fmtT(h.at)}</span> ${esc(h.what)}</div>`).join(""))}
        ${card("Decision trace", trace ? `<div class="small muted" style="margin-bottom:6px">Policies: ${Object.values(trace.policy_versions||{}).map(esc).join(" · ")}</div>` +
            trace.audit.map(a=>`<div class="small" style="padding:4px 0;border-bottom:1px solid var(--line2)"><span class="mono dim">${fmtT(a.sim_ts||a.ts)}</span> <span class="mono">${esc(a.kind)}</span> <span class="dim">${esc(JSON.stringify(a.data).slice(0,140))}</span></div>`).join("")
            : `<button class="btn sm" data-trace="${id}">Load persisted trace</button>`, `<a href="/api/incidents/${id}/trace" target="_blank">JSON</a>`)}
      </div>
    </div>`;
}

PAGES.loads = {
  crumbs: r => r.id ? `<a href="#/loads">Loads</a><span class="sep">/</span>${esc(r.id)}` : "Loads",
  render(r) { return r.id ? loadDetail(r.id) : loadList(); },
};
let loadFilter = "ACTIVE";
function loadList() {
  const f = {ACTIVE: a => a.status==="DISPATCHED", PROPOSED: a => ["PROPOSED","BLOCKED","DECLINED"].includes(a.status), DONE: a => ["COMPLETED","CANCELLED"].includes(a.status), ALL: () => true}[loadFilter];
  return `<div class="page-head"><div class="grow"><h1>Loads</h1><div class="sub">Every assignment is reforecast continuously. Proposed loads must pass compliance before release.</div></div></div>
    <div class="tabs">${[["ACTIVE","Active"],["PROPOSED","Proposed"],["DONE","Delivered"],["ALL","All"]].map(([k,l])=>`<a href="javascript:void 0" data-loadfilter="${k}" class="${loadFilter===k?"on":""}">${l} <span class="dim">${S.assignments.filter({ACTIVE: a => a.status==="DISPATCHED", PROPOSED: a => ["PROPOSED","BLOCKED","DECLINED"].includes(a.status), DONE: a => ["COMPLETED","CANCELLED"].includes(a.status), ALL: () => true}[k]).length}</span></a>`).join("")}</div>
    <div class="card"><div class="bd flush" style="overflow:auto">${loadsTable(S.assignments.filter(f))}</div></div>`;
}
function loadDetail(id) {
  const d = need("asg:"+id, `/api/assignments/${encodeURIComponent(id)}/detail`, 4000);
  if (!d) return loading;
  if (d.__error) return errBox(d);
  const a = d.row, ev = d.evaluation, L = d.load, terms = d.customer.terms;
  const comp = ev?.compliance;
  return `<div class="page-head"><div class="grow">
      <div class="row">${tag(a.status)} ${tag(a.verdict)} ${tag(ev?.feasibility?.service_tier)} <span class="mono dim">${a.id}</span></div>
      <h1 style="margin-top:6px">${a.load_id}: ${esc(short(a.origin))} → ${esc(short(a.destination))}</h1>
      <div class="sub">${esc(a.customer)} · ${esc(L.commodity)} · ${a.equipment.replace("_"," ")} · priority ${a.priority} · driver ${a.relay?`<a href="#/drivers/${a.driver_id}">${a.driver_id}</a> → <a href="#/drivers/${a.relay.driver_id}">${a.relay.driver_id}</a>`:a.driver_id?`<a href="#/drivers/${a.driver_id}">${a.driver_id}</a>`:"none"}</div></div></div>
    ${ev ? `<div class="kpis">
      <div class="kpi"><div class="l">Pickup</div><div class="v" style="font-size:18px">${fmtT(ev.pickup_eta)}</div><div class="s">depart ${fmtT(ev.depart_eta)}</div></div>
      <div class="kpi"><div class="l">Delivery ETA</div><div class="v" style="font-size:18px">${fmtT(ev.delivery_eta)}</div><div class="s">window ${fmtT(L.delivery_window.start)}–${fmtT(L.delivery_window.end)}</div></div>
      <div class="kpi ${ev.appointment_slack_minutes<0?"alert":""}"><div class="l">Appointment slack</div><div class="v">${hm(ev.appointment_slack_minutes)}</div></div>
      <div class="kpi ${ev.hos_reserve_minutes!=null&&ev.hos_reserve_minutes<30?"alert":""}"><div class="l">Legal reserve</div><div class="v">${hm(ev.hos_reserve_minutes)}</div><div class="s">tightest driver</div></div>
      <div class="kpi"><div class="l">Schedule risk</div><div class="v">${ev.risk?.score ?? "–"}</div><div class="s">${ev.risk?.band||""}</div></div>
      <div class="kpi"><div class="l">Revenue</div><div class="v" style="font-size:18px">${money(L.revenue)}</div></div></div>` : `<div class="callout info">No active plan to forecast (${esc(a.status)}).</div>`}
    ${ev ? card(`Forecast timeline: ${esc(ev.plan_title)}`, gantt(ev.timeline, {window: ev.delivery_window})) : ""}
    <div class="grid g2" style="margin-top:14px">
      ${comp ? card(`Compliance ${tag(comp.verdict)}`, `${comp.hard_failures.concat(comp.unknowns, comp.manual_review).map(f=>`<div class="callout"><b class="mono">${esc(f.code)}</b> ${esc(f.detail||"")}<div class="dim small">${esc(f.citation||"")}</div></div>`).join("")}
          ${comp.warnings.map(w=>`<div class="callout info"><b class="mono">${esc(w.code)}</b> ${esc(w.detail)}</div>`).join("")}
          <table>${comp.rule_trace.map(t=>`<tr><td class="mono small">${esc(t.subject)}</td><td class="mono small">${esc(t.rule)}</td><td>${tag(t.status)}</td><td class="small">${esc(t.detail)}<div class="dim">${esc(t.citation)}</div></td></tr>`).join("")}</table>`, esc(comp.ruleset)) : ""}
      <div class="grid" style="align-content:start">
        ${ev?.risk ? card(`Schedule risk ${tag(String(ev.risk.score), ev.risk.band)}`, `${ev.risk.factors.map(f=>`<div class="row" style="padding:4px 0"><span class="mono" style="min-width:34px">${f.points>0?"+":""}${f.points}</span><b>${esc(f.code.replace(/_/g," ").toLowerCase())}</b><span class="muted small">${esc(f.evidence)}</span></div>`).join("")||`<div class="muted">No risk factors.</div>`}<div class="dim small" style="margin-top:8px">${esc(ev.risk.disclaimer)}</div>`) : ""}
        ${ev ? card("Required stops", ev.required_stops.length ? `<table>${ev.required_stops.map(s=>`<tr><td class="mono">${fmtT(s.start)}</td><td class="mono">${s.driver_id}</td><td>${esc(s.label)}</td><td class="mono">${s.minutes}m</td></tr>`).join("")}</table>` : `<div class="muted">None.</div>`, "", true) : ""}
        ${card("Load & contract", `<dl class="kv"><dt>Pickup window</dt><dd class="mono">${fmtD(L.pickup_window.start)} – ${fmtT(L.pickup_window.end)}</dd>
          <dt>Delivery window</dt><dd class="mono">${fmtD(L.delivery_window.start)} – ${fmtT(L.delivery_window.end)}</dd>
          <dt>Load / unload</dt><dd>${L.load_minutes} / ${L.unload_minutes} min${a.pickup_delay?` (+${a.pickup_delay} min delay)`:""}</dd>
          <dt>Planned</dt><dd>${L.planned_miles} mi · ${hm(L.planned_drive_minutes)} driving</dd>
          <dt>Requirements</dt><dd>${[L.hazmat&&"hazmat (H)", L.tank&&"tank (N)", ...L.requires_credentials].filter(Boolean).join(", ")||"none"}</dd>
          <dt>Late terms</dt><dd>${tag("contract","contract")} $${terms.late_flat} + $${terms.late_per_hour}/h, cap $${terms.late_cap}</dd>
          <dt>Detention</dt><dd>${terms.detention_free_minutes} min free, then $${terms.detention_rate_per_hour}/h</dd>
          <dt>Reschedule allowed</dt><dd>${terms.reschedule_allowed?"yes":"no"}</dd></dl>`)}
        ${card("Assignment history", (d.assignment.history||[]).slice().reverse().map(h=>`<div class="small" style="padding:4px 0;border-bottom:1px solid var(--line2)"><span class="mono dim">${fmtT(h.at)}</span> ${esc(h.what || (h.changes||[]).join("; "))}</div>`).join("") || `<div class="muted">No changes.</div>`)}
        ${d.incidents.length ? card("Incidents", incidentRows(d.incidents), "", true) : ""}
      </div>
    </div>`;
}

PAGES.drivers = {
  crumbs: r => r.id ? `<a href="#/drivers">Drivers</a><span class="sep">/</span>${esc(r.id)}` : "Drivers",
  render(r) {
    if (!r.id) return `<div class="page-head"><div class="grow"><h1>Drivers</h1><div class="sub">Clocks projected to now from each driver's latest ELD snapshot. Stale snapshots never count as a pass.</div></div></div>
      <div class="card"><div class="bd flush" style="overflow:auto">${driversTable(S.drivers)}</div></div>`;
    return driverDetail(r.id);
  },
};
function driverDetail(id) {
  const d = need("drv:"+id, `/api/drivers/${encodeURIComponent(id)}/detail`, 4000);
  if (!d) return loading;
  if (d.__error) return errBox(d);
  const r = d.row, D = d.driver, e = D.eld;
  return `<div class="page-head"><div class="grow"><div class="row">${tag(r.availability)} ${r.stale?tag("ELD STALE","UNKNOWN"):tag("ELD fresh","PASS")} <span class="mono dim">${r.id}</span></div>
      <h1 style="margin-top:6px">${esc(D.name)}</h1><div class="sub">${esc(d.home_terminal)} · ${esc(r.location)} · ${D.notes?esc(D.notes):""}</div></div></div>
    <div class="grid g-main">
      <div class="grid" style="align-content:start">
        ${card("Hours of service (projected to now)", `${clockGauge("Drive left", r.drive_left, 660)}${clockGauge("Window left", r.window_left, 840)}${clockGauge(`Cycle left (${D.cycle_profile.replace("_","h/")}d)`, r.cycle_left, D.cycle_profile==="60_7"?3600:4200)}
          <div class="dim small" style="margin-top:6px">Computed by the deterministic HOS engine from the ELD snapshot below; no model involved.</div>`)}
        ${d.assignment ? card(`Current assignment: <a href="#/loads/${d.assignment.id}">${d.assignment.load_id}</a>`, loadsTable([d.assignment]), "", true) : card("Current assignment", `<div class="muted">Not on an active load.</div>`)}
        ${d.assignment ? card("Live position", lslot("drv-" + id, 300, {only: d.assignment.id})) : ""}
        ${card("Schedule", `<div class="board">${schedRow(id)}</div>`, `<a href="#/schedule">Whole fleet →</a>`)}
        ${card("Messages to driver", d.outbox.length ? d.outbox.map(o=>`<div class="small" style="padding:5px 0;border-bottom:1px solid var(--line2)">${tag(o.status)} <span class="mono dim">${fmtT(o.created_at)}</span> ${esc(o.body)}</div>`).join("") : `<div class="muted">None.</div>`)}
      </div>
      <div class="grid" style="align-content:start">
        ${card("ELD snapshot", `<dl class="kv"><dt>Duty status</dt><dd class="mono">${e.duty_status} since ${fmtD(e.status_since)}</dd>
          <dt>Shift started</dt><dd class="mono">${e.shift_start?fmtD(e.shift_start):"– (reset complete)"}</dd>
          <dt>Driving in shift</dt><dd class="mono">${hm(e.drive_minutes_in_shift)}</dd><dt>Since last break</dt><dd class="mono">${hm(e.drive_minutes_since_break)}</dd>
          <dt>Cycle used</dt><dd class="mono">${(e.cycle_used_minutes/60).toFixed(1)} h</dd><dt>Observed</dt><dd class="mono">${fmtD(e.observed_at)} (${r.eld_age_min} min ago)</dd>
          <dt>Source</dt><dd class="mono">${esc(e.source)} · feed ${esc(D.feed)}</dd></dl>`)}
        ${card("Qualifications", `<dl class="kv"><dt>CDL</dt><dd>Class ${D.cdl_class} · expires ${D.cdl_expires}</dd><dt>Endorsements</dt><dd>${D.endorsements.join(", ")||"none"}</dd>
          <dt>Restrictions</dt><dd>${D.restrictions.join(", ")||"none"}</dd><dt>Credentials</dt><dd>${D.credentials.join(", ")||"none"}</dd>
          <dt>Medical card</dt><dd>expires ${D.medical_expires}</dd><dt>Tractor</dt><dd>${D.tractor_id||"none"}${d.tractor?` · ${d.tractor.kind.replace("_"," ")}, ${d.tractor.transmission} · ${d.tractor.status}`:""}</dd>
          ${D.available_from?`<dt>Available from</dt><dd>${fmtD(D.available_from)}</dd>`:""}</dl>`)}
        ${card(`Schedule risk ${tag(String(d.risk.score), d.risk.band)}`, `${d.risk.factors.map(f=>`<div class="row" style="padding:4px 0"><span class="mono" style="min-width:34px">${f.points>0?"+":""}${f.points}</span><b>${esc(f.code.replace(/_/g," ").toLowerCase())}</b><span class="muted small">${esc(f.evidence)}</span></div>`).join("")||`<div class="muted">No risk factors.</div>`}
          <div class="dim small" style="margin-top:8px">${esc(d.risk.disclaimer)}</div>`)}
        ${card("Recent duty periods", D.recent_shifts.map(s=>`<div class="mono small" style="padding:3px 0">${fmtD(s.start)} → ${fmtD(s.end)}</div>`).join("") || `<div class="muted">None recorded.</div>`)}
      </div>
    </div>`;
}

function renderChat() {
  const el = $("#chatlog"); if (!el) return;
  el.innerHTML = CHAT.length ? CHAT.map(m => m.role==="user" ? `<div class="msg user">${esc(m.text)}</div>` :
    `<div class="msg bot ${m.pending?"muted":""}">${esc(m.text)}${m.steps?`<div class="steps">${esc(m.model||"")} · ${m.elapsed}s · ${m.steps.map(s=>`${s.tool}${s.blocked?" ⛔ approval required":""}`).join(" → ")||"no tools"}</div>`:""}</div>`).join("")
    : `<div class="empty">Ask a question. The assistant can look up anything in the dispatch system but cannot approve or execute plans.</div>`;
  el.scrollTop = el.scrollHeight;
  const m = $("#chat-model"); if (m && S) m.textContent = S.health?.llm?.ok ? "local · read-only tools" : "local model offline";
}
function renderIntake() {
  const el = $("#intake-out"); if (!el) return;
  if (!INTAKE) return;
  if (INTAKE.pending) { el.innerHTML = `<div class="muted">${esc(INTAKE.pending)}</div>`; return; }
  if (INTAKE.error) { el.innerHTML = `<div class="callout">${esc(INTAKE.error)}</div>`; return; }
  const ev = INTAKE.proposed_event, x = INTAKE.extracted || {};
  el.innerHTML = ev ? `<dl class="kv"><dt>Event</dt><dd class="mono">${ev.type}</dd><dt>Entity</dt><dd class="mono">${esc(ev.entity_type)} ${esc(ev.entity_id)}</dd>
      ${ev.payload.delay_minutes!=null?`<dt>Delay</dt><dd class="mono">+${ev.payload.delay_minutes} min</dd>`:""}${ev.payload.start?`<dt>New window</dt><dd class="mono">${esc(ev.payload.start)} → ${esc(ev.payload.end)}</dd>`:""}
      <dt>Reason</dt><dd>${esc(ev.payload.reason)}</dd><dt>Confidence</dt><dd>${Math.round((INTAKE.confidence||0)*100)}%</dd>
      <dt>Provenance</dt><dd class="mono small">${esc(ev.payload.provenance.kind)} · ${esc(ev.payload.provenance.model)} · sha ${esc(ev.payload.provenance.sha256)}</dd></dl>
      <div class="row" style="margin-top:10px"><button class="btn primary" id="intake-submit">Submit event</button><button class="btn ghost" id="intake-clear">Discard</button></div>`
    : `<div class="muted">No operational event found.</div><div class="dim small">${esc(x.reason||"")}</div>`;
}

PAGES.schedule = {
  crumbs: () => "Schedule",
  render() {
    const d = need("schedule", "/api/schedule", 3000);
    return `<div class="page-head"><div class="grow"><h1>Schedule</h1><div class="sub">Every driver's projected day: driving, duty, required breaks and rest, relay handoffs. Shaded = delivery window, red line = now. Click a driver to open them.</div></div></div>
      ${card("Dispatch board", d ? (d.__error ? errBox(d) : `<div class="board">${board(d)}</div>`) : loading)}`;
  },
};

PAGES.activity = {
  crumbs: r => "Activity & audit",
  render(r) {
    const tab = r.id || "events";
    let body;
    if (tab === "events") {
      const evs = need("events:"+actFilter.telemetry, `/api/events?limit=150&telemetry=${actFilter.telemetry}`, 2500);
      body = card("Event stream", evs ? (evs.map(evItem).join("") || `<div class="empty">No events.</div>`) : loading,
        `<label><input type="checkbox" data-telemetry ${actFilter.telemetry?"checked":""}> show ELD/GPS telemetry</label>`, true);
    } else if (tab === "outbox") {
      const ob = need("outbox", `/api/outbox?limit=100`, 2500);
      body = card("Outbox", ob ? (ob.map(o => `<div class="it"><span class="mono dim">#${o.id}<br>${fmtT(o.created_at)}</span><div>${tag(o.status, o.status==="queued"?"PROPOSED":o.status==="draft"?"MEDIUM":"EXECUTED")} <b>${esc(o.channel)}</b> → ${esc(o.recipient)} ${o.requires_approval?`<span class="dim">(needs approval to send)</span>`:""} ${o.incident_id?`<a href="#/incidents/${o.incident_id}">${o.incident_id}</a>`:""}
        <div class="muted small" style="white-space:pre-wrap;margin-top:3px">${esc(o.body)}</div></div></div>`).join("") || `<div class="empty">Outbox empty.</div>`) : loading,
        "driver requests queue automatically; customer messages wait for approval", true);
    } else {
      const au = need("audit", `/api/audit?limit=200`, 3000);
      body = card("Audit log (append-only)", au ? au.map(a => `<div class="it"><span class="mono dim">#${a.seq}<br>${fmtT(a.sim_ts||a.ts)}</span><div><span class="mono">${esc(a.kind)}</span> <span class="dim mono">${esc(a.ref)}</span>
        <div class="dim small" style="word-break:break-all">${esc(JSON.stringify(a.data).slice(0,260))}</div></div></div>`).join("") : loading, "also written to data/runtime/audit.jsonl", true);
    }
    return `<div class="page-head"><div class="grow"><h1>Activity &amp; audit</h1><div class="sub">Every event, message and decision is persisted and replayable.</div></div></div>
      <div class="tabs">${[["events","Events"],["outbox","Outbox"],["audit","Audit log"]].map(([k,l])=>`<a href="#/activity/${k}" class="${tab===k?"on":""}">${l}</a>`).join("")}</div>
      <div class="feed">${body}</div>`;
  },
};
function evItem(e) {
  const aff = (e.result?.affected||[]).map(a => `${a.load_id||a.assignment_id}: ${a.verdict||""}${a.incident&&a.incident.action&&a.incident.action!=="none"?` → ${a.incident.action} ${a.incident.incident_id||""}`:""}`).join("; ");
  return `<div class="it"><span class="mono dim">${fmtT(e.occurred_at)}</span><div><span class="mono">${e.type}</span> <b>${esc(e.entity_id)}</b> <span class="dim">${esc(e.source)}</span>
    ${e.payload?.delay_minutes?` <span style="color:#e3b341">+${e.payload.delay_minutes}m</span>`:""}${e.result?.status==="rejected"?` <span style="color:#ff7b72">rejected: ${esc(e.result.error)}</span>`:""}
    ${aff?`<div class="dim small">${esc(aff)}</div>`:""}</div></div>`;
}

PAGES.policies = {
  crumbs: () => "Policies & models",
  render() {
    const c = need("config", "/api/config", 60000);
    if (!c) return loading;
    const rs = c.ruleset, co = c.company_policy, cm = c.cost_model, rp = c.risk_policy, h = S.health || {};
    const lim = rs.limits;
    return `<div class="page-head"><div class="grow"><h1>Policies &amp; models</h1><div class="sub">Versioned rules decide legality. Every coefficient is visible. Models run on this GB10.</div></div>
      <button class="btn danger" id="reset-demo">Reset demo fleet</button></div>
      <div class="grid g2">
        ${card(`Hours-of-service ruleset <span class="mono dim">${esc(c.versions.ruleset)}</span>`, `<table>
          ${[["Driving limit", lim.driving_minutes, "DRIVE_LIMIT"], ["Duty window", lim.window_minutes, "SHIFT_LIMIT"], ["Break after driving", lim.break_after_driving_minutes, "BREAK_REQUIRED"], ["Break length", lim.break_minutes, "BREAK_REQUIRED"], ["Off-duty reset", lim.off_duty_reset_minutes, "OFF_DUTY_RESET"], ["Restart", lim.restart_minutes, "RESTART"]].map(([l,v,k])=>`<tr><td>${l}</td><td class="mono">${hm(v)}</td><td class="dim small">${esc(rs.citations[k])}</td></tr>`).join("")}
          ${Object.entries(lim.cycles).map(([k,v])=>`<tr><td>Cycle ${k.replace("_","h / ")}d</td><td class="mono">${v.minutes/60} h</td><td class="dim small">${esc(rs.citations.CYCLE_LIMIT)}</td></tr>`).join("")}</table>
          <div class="callout info small" style="margin-top:10px">Unsupported exceptions return MANUAL_REVIEW: ${rs.unsupported_exceptions.join(", ").replace(/_/g," ")}. ${rs.verified_against_regulation?"":"Not yet verified against the current eCFR."}</div>`, "", false)}
        ${card(`Company policy <span class="mono dim">${esc(c.versions.company_policy)}</span>`, `<dl class="kv">
          <dt>Max driving</dt><dd>${co.max_driving_minutes?hm(co.max_driving_minutes):"legal limit"}</dd><dt>Max window</dt><dd>${co.max_window_minutes?hm(co.max_window_minutes):"legal limit"}</dd>
          <dt>Min legal reserve</dt><dd>${co.min_hos_reserve_minutes} min (hard)</dd><dt>Low reserve warning</dt><dd>${co.low_reserve_warning_minutes} min</dd>
          <dt>ELD freshness</dt><dd>${co.max_eld_age_minutes} min, older is UNKNOWN</dd><dt>Min service slack</dt><dd>${co.min_service_slack_minutes} min (below = AT RISK)</dd>
          <dt>Recommendation TTL</dt><dd>${co.recommendation_ttl_minutes} min</dd></dl>`)}
        ${card(`Cost model <span class="mono dim">${esc(c.versions.cost_model)}</span>`, `<table>${Object.entries(cm.coefficients).map(([k,v])=>`<tr><td>${k.replace(/_/g," ")}</td><td class="mono">${String(v.value).includes(".")||k.includes("per")?"$"+v.value:"$"+v.value}</td><td>${tag(v.basis, v.basis)}</td></tr>`).join("")}</table>
          <h3 style="margin-top:12px">Operating buffers</h3><table>${Object.entries(cm.operations).map(([k,v])=>`<tr><td>${k.replace(/_/g," ")}</td><td class="mono">${v}</td></tr>`).join("")}</table>`)}
        ${card(`Schedule-risk policy <span class="mono dim">${esc(c.versions.risk_policy)}</span>`, `<table>${Object.entries(rp.factors).map(([k,v])=>`<tr><td>${k.replace(/_/g," ").toLowerCase()}</td><td class="mono">${v.points>0?"+":""}${v.points}</td></tr>`).join("")}</table>
          <div class="row" style="margin-top:10px">${rp.bands.map(b=>tag(`${b.name} ≥ ${b.min}`, b.name)).join(" ")}</div><div class="dim small" style="margin-top:8px">${esc(rp.disclaimer)}</div>`)}
        ${card("Customer terms", `<table><tr><th>Customer</th><th>Late penalty</th><th>Detention</th><th>Reschedule</th></tr>${c.customers.map(x=>`<tr><td>${esc(x.name)}</td><td class="mono small">$${x.terms.late_flat} + $${x.terms.late_per_hour}/h (cap $${x.terms.late_cap})</td><td class="mono small">${x.terms.detention_free_minutes}m free, $${x.terms.detention_rate_per_hour}/h</td><td>${x.terms.reschedule_allowed?"yes":"no"}</td></tr>`).join("")}</table>`, "", true)}
        ${card("Local models", `<dl class="kv"><dt>Planner</dt><dd>${tag(h.llm?.ok?"online":"offline", h.llm?.ok?"PASS":"FAIL")} <span class="mono">${esc((h.llm?.models||[]).join(", "))}</span> via vLLM :8000</dd>
          <dt>Uses</dt><dd class="muted">tool-calling agent, incident explanations, text intake (schema-constrained JSON)</dd>
          <dt>Vision</dt><dd>${tag(h.vlm?.ok?"online":"offline", h.vlm?.ok?"PASS":"FAIL")} <span class="mono">${esc((h.vlm?.models||[]).join(", "))}</span> via Ollama :11434</dd>
          <dt>Uses</dt><dd class="muted">document and photo intake</dd>
          <dt>Never used for</dt><dd class="muted">legality, ETAs, costs, risk scores or approvals</dd>
          <dt>Monitor</dt><dd>${S.runtime.ticks} ticks${S.runtime.last_error?` · <span style="color:#ff7b72">${esc(S.runtime.last_error)}</span>`:""}</dd></dl>`)}
      </div>`;
  },
};

/* ---------------------------------------------------------------- render loop */
function draw(force=false) {
  if (!S) return;
  const r = route(), p = PAGES[r.page] || PAGES.overview;
  renderNav(r);
  $("#crumbs").innerHTML = p.crumbs(r);
  const crit = S.incidents.find(i => i.status==="OPEN" && ["HIGH","CRITICAL"].includes(i.severity));
  const showBanner = crit && !(r.page==="incidents" && r.id===crit.id);
  $("#banner").innerHTML = showBanner ? `<div class="callout"><b>${tag(crit.severity)} ${esc(crit.id)}</b> ${esc(crit.title)} <a href="#/incidents/${crit.id}" style="margin-left:8px">Review →</a></div>` : "";
  const html = p.render(r);
  const pageKey = r.page + "/" + (r.id||"");
  if (html === lastHtml && pageKey === lastPage && !force) { refreshMaps(); return; }
  const view = $("#view");
  const openD = new Set([...view.querySelectorAll("details[open][data-k]")].map(d => d.dataset.k));
  const vals = {}; view.querySelectorAll("input[id],textarea[id]").forEach(i => { if (i.type !== "file") vals[i.id] = i.value; });
  const focus = document.activeElement && document.activeElement.id;
  const sy = window.scrollY;
  view.innerHTML = html;
  view.querySelectorAll("details[data-k]").forEach(d => { if (openD.has(d.dataset.k)) d.open = true; });
  Object.entries(vals).forEach(([k,v]) => { const el = document.getElementById(k); if (el) el.value = v; });
  if (focus) { const el = document.getElementById(focus); if (el) el.focus(); }
  if (pageKey === lastPage) window.scrollTo(0, sy); else window.scrollTo(0, 0);
  lastHtml = html; lastPage = pageKey;
  mountSlots();
  if (p.after) p.after(r);
}
function refreshMaps() { document.querySelectorAll("[data-lslot]").forEach(sl => { const m = LMAPS[sl.dataset.lslot]; if (m && m.fm) m.fm.draw(); }); }
function tickClock() {
  if (!S) return;
  $("#clock").innerHTML = `${simNow().toLocaleString("en-US",{timeZone:TZ,weekday:"short",month:"short",day:"numeric",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false})}<small>ET · ${S.clock.paused?"paused":S.clock.speed+"×"}</small>`;
}
function renderChrome() {
  const h = S.health || {}, llm = h.llm||{}, vlm = h.vlm||{};
  $("#h-llm").className = "dot " + (llm.ok?"ok":"bad"); $("#h-llm-t").textContent = llm.ok ? "vLLM · Qwen3.6-35B-A3B" : "vLLM offline";
  $("#h-vlm").className = "dot " + (vlm.ok?"ok":"bad"); $("#h-vlm-t").textContent = vlm.ok ? "Ollama · qwen3-vl:30b" : "Ollama offline";
  $("#h-rt").className = "dot " + (S.runtime.last_error?"bad":"ok"); $("#h-rt-t").textContent = S.runtime.last_error ? "monitor error" : `monitor · ${S.runtime.ticks} ticks`;
  document.querySelectorAll("#speeds button").forEach(b => b.classList.toggle("on", b.dataset.s==="pause" ? S.clock.paused : (!S.clock.paused && +b.dataset.s===S.clock.speed)));
}
async function refresh() {
  try {
    S = await api("/api/state"); nowMs = Date.now();
    if (!MAP) MAP = await api("/api/map");
    renderChrome(); tickClock();
    const r = route(); if (r.page==="incidents" && r.id) need("inc:"+r.id, `/api/incidents/${encodeURIComponent(r.id)}`, 3000);
    draw();
  } catch (e) { console.error(e); }
}

/* ---------------------------------------------------------------- actions (event delegation) */
document.addEventListener("click", async ev => {
  const t = ev.target.closest("[data-href],[data-approve],[data-reject],[data-explain],[data-override],[data-trace],[data-scenario],[data-incfilter],[data-loadfilter],[data-ask],[data-select],[data-colorby],[data-docktab],#chat-go,#intake-go,#doc-go,#intake-submit,#intake-clear,#reset-demo,#chat-fab,#dock-close,#theme");
  if (!t) return;
  if (t.id === "chat-fab") { openDock(true); return; }
  if (t.id === "dock-close") { openDock(false); return; }
  if (t.id === "theme") { setTheme(isLight() ? "dark" : "light"); return; }
  if (t.dataset.docktab) { document.querySelectorAll("[data-docktab]").forEach(a => a.classList.toggle("on", a === t)); $("#dock-chat").hidden = t.dataset.docktab !== "chat"; $("#dock-intake").hidden = t.dataset.docktab !== "intake"; return; }
  if (t.dataset.select) { SEL = SEL === t.dataset.select ? null : t.dataset.select; return draw(true); }
  if (t.dataset.colorby) { COLOR_BY = t.dataset.colorby; return draw(true); }
  if (!t) return;
  if (t.dataset.href && !ev.target.closest("a")) { location.hash = t.dataset.href; return; }
  if (t.dataset.approve) {
    if (!actor()) return toast("Enter your dispatcher name in the header first.");
    t.disabled = true;
    try {
      const r = await api(`/api/plans/${t.dataset.approve}/approve-and-execute`, {method:"POST", body:JSON.stringify({actor: actor()})});
      toast(`<b>Executed ${esc(t.dataset.approve)}</b><br>${r.changes.map(esc).join("<br>")}<br><span class="muted">re-validated ${r.revalidation.verdict} · delivery ${fmtT(r.revalidation.delivery_eta)}</span>`, 7000);
    } catch (e) { toast(`<span style="color:#ff7b72">Blocked:</span> ${esc(e.message)}`, 7000); }
    invalidate("inc:"); invalidate("asg:"); return refresh();
  }
  if (t.dataset.reject) {
    const reason = prompt("Why are you rejecting this plan?"); if (reason === null) return;
    try { await api(`/api/plans/${t.dataset.reject}/reject`, {method:"POST", body:JSON.stringify({actor: actor(), reason})}); } catch (e) { toast(esc(e.message)); }
    invalidate("inc:"); return refresh();
  }
  if (t.dataset.explain) {
    S.explaining.push(t.dataset.explain); draw(true);
    try { await api(`/api/incidents/${t.dataset.explain}/explain`, {method:"POST"}); } catch (e) { toast(`Local model error: ${esc(e.message)}`, 7000); }
    invalidate("inc:"); return refresh();
  }
  if (t.dataset.override) {
    const reason = prompt("Override reason (recorded in the audit trail)?"); if (!reason) return;
    try { await api(`/api/incidents/${t.dataset.override}/override`, {method:"POST", body:JSON.stringify({actor: actor(), reason})}); toast("Override recorded."); }
    catch (e) { toast(`<span style="color:#ff7b72">${esc(e.message)}</span>`); }
    invalidate("inc:"); return refresh();
  }
  if (t.dataset.trace) { need("trace:"+t.dataset.trace, `/api/incidents/${t.dataset.trace}/trace`, 1e9); return; }
  if (t.dataset.incfilter) { incFilter = t.dataset.incfilter; return draw(true); }
  if (t.dataset.loadfilter) { loadFilter = t.dataset.loadfilter; return draw(true); }
  if (t.dataset.scenario) {
    t.disabled = true;
    try {
      const r = await api(`/api/scenarios/${t.dataset.scenario}/inject`, {method:"POST"});
      const res = r.results[0], inc = res.affected.map(a => a.incident).find(i => i && ["opened","updated"].includes(i.action));
      if (res.status === "duplicate") toast("Already injected. Reset the demo (Policies & models page) to replay it.");
      else if (res.status === "rejected") toast(`<span style="color:#ff7b72">Rejected:</span> ${esc(res.error)}. Reset the demo to replay from 06:30.`, 7000);
      else if (inc) { toast(`Processed in ${res.processing_ms} ms → ${inc.action} <b>${inc.incident_id}</b>`); location.hash = `#/incidents/${inc.incident_id}`; }
      else toast(`Processed in ${res.processing_ms} ms.`);
    } catch (e) { toast(esc(e.message)); }
    t.disabled = false; invalidate("inc:"); return refresh();
  }
  if (t.dataset.ask) { openDock(true); $("#chat-in").value = t.dataset.ask; return ask(); }
  if (t.id === "chat-go") return ask();
  if (t.id === "intake-go") {
    const text = $("#intake-text").value.trim(); if (!text) return;
    INTAKE = {pending: "Parsing with Qwen3.6…"}; renderIntake();
    try { INTAKE = await api("/api/intake/text", {method:"POST", body:JSON.stringify({text, source:"dispatcher-paste"})}); } catch (e) { INTAKE = {error: e.message}; }
    return renderIntake();
  }
  if (t.id === "doc-go") {
    const f = $("#doc-file").files[0]; if (!f) return toast("Choose an image first.");
    INTAKE = {pending: `Reading ${f.name} with qwen3-vl…`}; renderIntake();
    const fd = new FormData(); fd.append("file", f); fd.append("source", "upload");
    try { INTAKE = await api("/api/intake/document", {method:"POST", body: fd}); } catch (e) { INTAKE = {error: e.message}; }
    return renderIntake();
  }
  if (t.id === "intake-submit") {
    const e = INTAKE.proposed_event;
    try {
      const res = await api("/api/events", {method:"POST", body:JSON.stringify({type:e.type, entity_type:e.entity_type, entity_id:e.entity_id, payload:e.payload, source:e.source})});
      const inc = (res.affected||[]).map(a => a.incident).find(i => i && ["opened","updated"].includes(i.action));
      if (res.status === "rejected") toast(`Rejected: ${esc(res.error)}`);
      else if (inc) { toast(`Event processed → ${inc.action} <b>${inc.incident_id}</b>`); location.hash = `#/incidents/${inc.incident_id}`; }
      else toast(`Event processed in ${res.processing_ms} ms; no incident needed.`);
      INTAKE = null; renderIntake();
    } catch (err) { toast(esc(err.message)); }
    invalidate("inc:"); return refresh();
  }
  if (t.id === "intake-clear") { INTAKE = null; $("#intake-out").innerHTML = `<div class="muted">Nothing parsed yet.</div>`; return; }
  if (t.id === "reset-demo") {
    if (!confirm("Reset the synthetic fleet, clear incidents and restart the clock at 06:30?")) return;
    await api("/api/reset", {method:"POST"}); CHAT.length = 0; INTAKE = null; Object.keys(cache).forEach(k => k!=="scen" && delete cache[k]);
    toast("Demo reset."); location.hash = "#/overview"; return refresh();
  }
});
document.addEventListener("change", e => {
  if (e.target.matches("[data-telemetry]")) { actFilter.telemetry = e.target.checked; draw(true); }
  if (e.target.id === "actor") { try { localStorage.setItem("dg.actor", e.target.value); } catch (_) {} }
});
document.addEventListener("keydown", e => { if (e.key === "Enter" && e.target.id === "chat-in") ask(); });
async function ask() {
  const inp = $("#chat-in"), message = inp.value.trim(); if (!message) return;
  inp.value = "";
  CHAT.push({role:"user", text:message}); const pend = {role:"bot", text:"Thinking with local tools…", pending:true}; CHAT.push(pend); renderChat();
  const r0 = route();
  const incId = (S.incidents.find(i => message.includes(i.id)) || {}).id || (r0.page === "incidents" && r0.id) || (S.incidents.find(i => i.status === "OPEN" && i.plans && i.plans.length) || {}).id;
  try {
    const r = await api("/api/agent/chat", {method:"POST", body:JSON.stringify({message, incident_id: incId || null})});
    Object.assign(pend, {text: r.answer, pending:false, steps: r.steps, model: r.model, elapsed: r.elapsed_s});
  } catch (e) { Object.assign(pend, {text: "Local model error: " + e.message, pending:false}); }
  renderChat();
}
document.querySelectorAll("#speeds button").forEach(b => b.onclick = async () => {
  const s = b.dataset.s;
  await api("/api/clock", {method:"POST", body:JSON.stringify(s==="pause" ? {paused:true} : {speed:+s, paused:false})});
  refresh();
});
$("#adv").onclick = async () => { await api("/api/clock", {method:"POST", body:JSON.stringify({advance_minutes:15})}); refresh(); };
$("#menu").onclick = () => document.querySelector(".side").classList.toggle("open");
try { $("#actor").value = localStorage.getItem("dg.actor") || "dispatcher"; } catch (_) {}
window.addEventListener("hashchange", () => { document.querySelector(".side").classList.remove("open"); draw(true); });

need("scen", "/api/scenarios", 1e9);
refresh();
setInterval(refresh, 2500);
setInterval(tickClock, 500);

/* ---- chat dock & theme ---- */
function openDock(open) {
  $("#dock").classList.toggle("open", open); $("#chat-fab").hidden = open;
  if (open) { renderChat(); setTimeout(() => $("#chat-in").focus(), 50); }
}
$("#chips").innerHTML = ["What needs my attention right now?", "Why can't D-01 keep DG-204 as planned?", "Who else could legally take DG-204, and at what cost?", "Which drivers are closest to their hours limits?"]
  .map(q => `<button class="chip" data-ask="${esc(q)}">${esc(q)}</button>`).join("");
function setTheme(t) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("dg.theme", t); } catch (_) {}
  refreshMaps();
}
try { setTheme(localStorage.getItem("dg.theme") || "dark"); } catch (_) { setTheme("dark"); }

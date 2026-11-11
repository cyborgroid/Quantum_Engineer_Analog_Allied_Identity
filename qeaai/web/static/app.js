/* QEAAI OS - 브라우저 화면.
 * 서버(/api/stream)가 200 ms 마다 보내는 커널 상태를 받아서
 * VR/Game 3D 뷰, 근육 신호, Allied Identity, 전원, QE, 트윈, 네트워크를 그린다.
 * 외부 라이브러리 없음 (Canvas 2D 로 직접 3D 투영). */
"use strict";

const $ = (id) => document.getElementById(id);
const C = {
  accent: "#4fd1c5", amber: "#f6ad55", violet: "#9f7aea", blue: "#63b3ed",
  ok: "#68d391", danger: "#fc5c65", text: "#d8e2ec", muted: "#7b8a99", line: "#1f2c39",
};
const CH_COLORS = [C.accent, C.amber, C.violet, C.blue];
const LABEL = { rest: "휴식", fist: "주먹", open: "펴기", wrist_left: "손목 좌", wrist_right: "손목 우" };
const GESTURE_ORDER = ["rest", "fist", "open", "wrist_left", "wrist_right"];
const rad = (d) => (d * Math.PI) / 180;
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const lerp = (a, b, t) => a + (b - a) * t;
const lerpAngle = (a, b, t) => a + ((((b - a) % 360) + 540) % 360 - 180) * t;

let S = null;          // 최신 상태
let info = null;       // 셀 사양 / QE 곡선
let prevF = null, curF = null, recvAt = 0;
let dirty = true;
const EMG_MAX = 250;
const emgBuf = [[], [], [], []];

/* ---------- 캔버스 헬퍼 ---------- */
function fit(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return [ctx, w, h];
}

/* ---------- 네트워크 ---------- */
function connect() {
  const es = new EventSource("/api/stream");
  es.onopen = () => $("conn").classList.add("on");
  es.onerror = () => $("conn").classList.remove("on");
  es.onmessage = (ev) => onState(JSON.parse(ev.data));
}

async function post(cmd) {
  try {
    await fetch("/api/control", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cmd) });
  } catch (e) { console.warn("control failed", e); }
}

function onState(st) {
  if (S && st.t < S.t) { // 리셋됨
    emgBuf.forEach((b) => (b.length = 0));
    curF = null;
  }
  prevF = curF || st.flight;
  curF = st.flight;
  recvAt = performance.now();
  st.emg.forEach((ch, i) => {
    const b = emgBuf[i];
    b.push(...ch);
    if (b.length > EMG_MAX) b.splice(0, b.length - EMG_MAX);
  });
  S = st;
  dirty = true;
  updateDom(st);
}

/* ---------- DOM 패널 ---------- */
function buildScores() {
  $("scores").innerHTML = GESTURE_ORDER.map(
    (g) => `<div class="score" data-g="${g}"><span>${LABEL[g]}</span><div class="track"><i></i></div><span>0%</span></div>`
  ).join("");
}

function setSeg(id, attr, value) {
  document.querySelectorAll(`#${id} button`).forEach((b) => b.classList.toggle("on", b.dataset[attr] === value));
}

function fmtTime(s) {
  if (!isFinite(s)) return "∞";
  const m = Math.floor(s / 60), r = Math.floor(s % 60);
  return `${m}분 ${String(r).padStart(2, "0")}초`;
}

function updateDom(st) {
  $("clock").textContent = `t = ${st.t.toFixed(1)} s`;
  $("script").textContent = st.mode === "demo" ? st.script : `MANUAL · ${LABEL[st.gesture]}`;
  setSeg("wearerSeg", "wearer", st.wearer);
  setSeg("modeSeg", "mode", st.mode);
  document.querySelectorAll("#pad button").forEach((b) => b.classList.toggle("sensed", b.dataset.g === st.gesture));

  $("actBar").style.width = `${(st.activation * 100).toFixed(0)}%`;
  $("actVal").textContent = st.activation.toFixed(2);

  const d = st.decision;
  const badge = $("ctrlBadge");
  if (!d) {
    $("intent").textContent = "—";
    badge.textContent = "BROWNOUT"; badge.className = "badge warn";
  } else {
    $("intent").textContent = LABEL[d.intent] || d.intent;
    badge.textContent = d.trusted ? "CONTROL" : "LOCKED";
    badge.className = `badge ${d.trusted ? "ok" : "bad"}`;
    document.querySelectorAll(".score").forEach((row) => {
      const p = d.scores[row.dataset.g] || 0;
      row.classList.toggle("top", row.dataset.g === d.intent);
      row.querySelector("i").style.width = `${(p * 100).toFixed(1)}%`;
      row.lastElementChild.textContent = `${(p * 100).toFixed(0)}%`;
    });
    $("trustVal").textContent = d.trust.toFixed(2);
    const tb = $("trustBar");
    tb.style.width = `${(d.trust * 100).toFixed(1)}%`;
    tb.classList.toggle("low", !d.trusted);
    $("trustMark").style.left = `${d.trust_threshold * 100}%`;
    $("dist").textContent = `${d.distance.toFixed(2)} / ${d.threshold.toFixed(2)}`;
    $("auth").innerHTML = d.authenticated ? '<span class="yes">✓ 본인 패턴</span>' : '<span class="no">✗ 패턴 불일치</span>';
  }
  $("owner").textContent = st.owner || "—";
  $("wearer").innerHTML = st.wearer === "owner" ? '<span class="yes">본인</span>' : '<span class="no">타인</span>';

  const p = st.power;
  $("powerKv").innerHTML = [
    ["팩 전압", `${p.pack_v.toFixed(2)} / ${p.pack_max_v.toFixed(1)} V`],
    ["셀 전압", `${p.cell_v.toFixed(3)} V`],
    ["저장 에너지", `${(p.energy_j * 1000).toFixed(0)} mJ`],
    ["부하", `${p.load_mw.toFixed(2)} mW`],
    ["근육 발전", `${p.harvest_uw.toFixed(1)} µW`],
    ["팩 전류", `${p.current_ma.toFixed(3)} mA`],
    ["예상 구동", fmtTime(p.runtime_s)],
  ].map(([k, v]) => `<dt>${k}</dt><dd class="num">${v}</dd>`).join("");
  $("brownout").hidden = !p.brownout;

  $("twinCards").innerHTML = st.twin.map(twinCard).join("");
  const nReal = st.twin.filter((d) => d.real).length;
  $("twinCount").textContent = `실측 ${nReal} · 가상 ${st.twin.length - nReal}`;
  updateWorld(st);
  const m = st.mqtt;
  $("mqttStat").innerHTML = !m || !m.enabled
    ? `MQTT 꺼짐${m && m.error ? " · " + escapeHtml(m.error) : ""}`
    : `MQTT <b>${escapeHtml(m.url)}</b>${m.auth ? " 🔒" : ""} · 장치 ${m.clients.length}${m.clients.length ? " (" + m.clients.map(escapeHtml).join(", ") + ")" : ""} · in ${m.in} / out ${m.out}`;

  const n = st.network;
  $("netStat").textContent = `delivered ${n.delivered.toLocaleString()} · rejected ${n.rejected}`;
  $("netLog").innerHTML = n.recent.slice().reverse().map((m) => {
    const cls = m.topic.startsWith("neural") ? "t-neural" : "t-device";
    return `<li><span class="seq">#${m.seq}</span><span class="${cls}">${m.topic}</span><span class="sig">${m.sig}…</span></li>`;
  }).join("");
}

const SOURCE_LABEL = { sim: "시뮬레이션", sysfs: "이 컴퓨터 센서", "open-meteo": "Open-Meteo", mqtt: "MQTT" };
const FIELD_LABEL = {
  soc: ["SOC", (v) => `${(v * 100).toFixed(1)}%`], pack_v: ["팩", (v) => `${v.toFixed(2)} V`],
  harvest_uW: ["발전", (v) => `${v.toFixed(1)} µW`], alt_m: ["고도", (v) => `${v.toFixed(1)} m`],
  speed: ["속도", (v) => `${v.toFixed(1)} m/s`], heading: ["방위", (v) => `${v.toFixed(0)}°`],
  dist_m: ["거리", (v) => `${v.toFixed(0)} m`], linked: ["링크", (v) => (v ? "연결" : "범위 밖")],
  cpu_temp_c: ["CPU 온도", (v) => `${v.toFixed(1)} °C`], cpu_load_pct: ["CPU 사용", (v) => `${v.toFixed(0)}%`],
  mem_used_pct: ["메모리", (v) => `${v.toFixed(0)}%`], nvme_temp_c: ["SSD 온도", (v) => `${v.toFixed(1)} °C`],
  battery_pct: ["배터리", (v) => `${v.toFixed(0)}%`], battery_status: ["충전", (v) => ({ Charging: "충전 중", Discharging: "방전 중", Full: "완충", "Not charging": "대기" }[v] || v)],
  battery_power_w: ["전력", (v) => `${v.toFixed(1)} W`], net_rx_kbps: ["수신", (v) => `${v.toFixed(0)} kbps`],
  net_tx_kbps: ["송신", (v) => `${v.toFixed(0)} kbps`], ac_online: ["어댑터", (v) => (v ? "연결" : "분리")],
  description: ["날씨", (v) => v], temp_c: ["기온", (v) => `${v.toFixed(1)} °C`], feels_like_c: ["체감", (v) => `${v.toFixed(1)} °C`],
  humidity_pct: ["습도", (v) => `${v.toFixed(0)}%`], wind_mps: ["바람", (v) => `${v.toFixed(1)} m/s`],
  wind_dir_deg: ["풍향", (v) => `${compass(v)} (${v.toFixed(0)}°)`], gust_mps: ["돌풍", (v) => `${v.toFixed(1)} m/s`],
  cloud_pct: ["구름", (v) => `${v.toFixed(0)}%`], pressure_hpa: ["기압", (v) => `${v.toFixed(0)} hPa`],
};
const HIDDEN_FIELDS = new Set(["id", "kind", "source", "real", "age_s", "x", "y", "host", "observed", "place", "is_day",
  "net_iface", "uptime_h", "load_avg_1m", "battery_voltage_v", "brownout", "airborne", "precip_mm", "uptime_s"]);
const CARD_ROWS = { edge_gateway: 6, weather_station: 6 };

function compass(deg) {
  return ["북", "북동", "동", "남동", "남", "남서", "서", "북서"][Math.round(((deg % 360) + 360) % 360 / 45) % 8];
}
function fmtVal(k, v) {
  if (FIELD_LABEL[k]) return FIELD_LABEL[k];
  return [k, (x) => (typeof x === "number" ? (Number.isInteger(x) ? String(x) : x.toFixed(2)) : String(x))];
}
function escapeHtml(t) {
  return String(t).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function twinCard(d) {
  const rows = [];
  Object.entries(d).forEach(([k, v]) => {
    if (HIDDEN_FIELDS.has(k) || v === null || v === undefined || typeof v === "object") return;
    const [label, fmt] = fmtVal(k, v);
    rows.push([label, fmt(v)]);
  });
  const limit = CARD_ROWS[d.kind] || 4;
  let cls = `card ${d.real ? "real" : "sim"}`;
  if (d.brownout) cls += " alarm";
  if (d.kind === "iot_beacon" && d.linked) cls += " linked";
  const stale = d.real && d.age_s !== null && d.age_s > (d.source === "open-meteo" ? 1200 : 15);
  if (stale) cls += " stale";
  const age = d.age_s === null ? "" : d.age_s < 2 ? "실시간" : `${d.age_s.toFixed(0)}초 전`;
  const title = d.kind === "edge_gateway" && d.host ? `${d.id} · ${d.host}` : d.kind === "weather_station" && d.place ? `${d.id} · ${d.place}` : d.id;
  return `<div class="${cls}"><div class="head"><span class="id" title="${escapeHtml(title)}">${escapeHtml(title)}</span>` +
    `<span class="badge-src ${d.real ? "real" : "sim"}">${d.real ? "REAL" : "SIM"}</span></div>` +
    `<div class="meta">${escapeHtml(d.kind)} · ${SOURCE_LABEL[d.source] || escapeHtml(d.source)}${age ? " · " + age : ""}</div>` +
    rows.slice(0, limit).map(([k, v]) => `<div class="row"><span>${escapeHtml(k)}</span><b>${escapeHtml(v)}</b></div>`).join("") + `</div>`;
}

function updateWorld(st) {
  const e = st.env;
  const el = $("world");
  if (!e) { el.textContent = "실제 세계 연결 꺼짐 (--offline)"; return; }
  const w = e.weather;
  const sun = e.sun.elevation_deg;
  const phase = sun > 6 ? "☀" : sun > -6 ? "◐" : "☾";
  const parts = [`📍 ${e.location.name}`, `${e.local_time.slice(11)} ${phase} 태양 ${sun.toFixed(0)}°`];
  if (w) parts.push(`${w.description} ${w.temperature_2m.toFixed(1)}°C`, `바람 ${compass(w.wind_direction_10m)} ${w.wind_speed_10m.toFixed(1)} m/s`);
  else parts.push(e.weather_enabled ? "날씨 수신 중…" : "날씨 꺼짐");
  el.textContent = parts.join(" · ");
  el.title = `밴드 전해질 온도 ${st.electrolyte_c.toFixed(1)}°C (손목 피부 33°C 와 실제 기온의 혼합)`;
}

/* ---------- 근전도 ---------- */
function drawEMG() {
  const [ctx, W, H] = fit($("emg"));
  ctx.clearRect(0, 0, W, H);
  const lane = H / 4, scale = (lane / 2) * 0.9 / 1.6; // ±1.6 mV
  for (let c = 0; c < 4; c++) {
    const mid = lane * c + lane / 2;
    ctx.strokeStyle = C.line; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, mid); ctx.lineTo(W, mid); ctx.stroke();
    const buf = emgBuf[c];
    if (buf.length > 1) {
      ctx.strokeStyle = CH_COLORS[c]; ctx.lineWidth = 1.2;
      ctx.beginPath();
      buf.forEach((v, i) => {
        const x = (i / (EMG_MAX - 1)) * W, y = mid - clamp(v * scale, -lane / 2, lane / 2);
        i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      });
      ctx.stroke();
    }
    if (S) { // 채널 라벨 (신호 위에 읽히도록 배경)
      ctx.font = "11px 'Pretendard Variable', system-ui";
      const label = S.channels[c], w = ctx.measureText(label).width + 8;
      ctx.fillStyle = "rgba(10,16,23,0.85)"; ctx.fillRect(3, lane * c + 3, w, 15);
      ctx.fillStyle = C.muted; ctx.fillText(label, 7, lane * c + 14);
    }
  }
}

/* ---------- 3D VR / Game ---------- */
function makeCamera(f, W, H) {
  const h = rad(f.heading_deg);
  const fx = Math.sin(h), fy = Math.cos(h);
  const plane = [f.x, f.y, f.altitude_m + 1.2];
  const cam = {
    x: plane[0] - fx * 28, y: plane[1] - fy * 28, z: plane[2] + 7,
    h, p: -0.2 + rad(f.pitch_deg) * 0.5, b: rad(f.bank_deg) * 0.25, focal: H * 1.0, W, H,
  };
  const sh = Math.sin(cam.h), ch = Math.cos(cam.h);
  const sp = Math.sin(cam.p), cp = Math.cos(cam.p);
  const sb = Math.sin(cam.b), cb = Math.cos(cam.b);
  cam.toCam = (P) => {
    const dx = P[0] - cam.x, dy = P[1] - cam.y, dz = P[2] - cam.z;
    const fw = dx * sh + dy * ch, r = dx * ch - dy * sh;
    const f2 = fw * cp + dz * sp, u2 = dz * cp - fw * sp;
    return [r * cb - u2 * sb, u2 * cb + r * sb, f2];
  };
  cam.proj = (c) => [W / 2 + (c[0] / c[2]) * cam.focal, H / 2 - (c[1] / c[2]) * cam.focal];
  return { cam, plane };
}

const NEAR = 0.5;
function clipNear(a, b) {
  const t = (NEAR - a[2]) / (b[2] - a[2]);
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, NEAR];
}
function segment(cam, P, Q) {
  let a = cam.toCam(P), b = cam.toCam(Q);
  if (a[2] < NEAR && b[2] < NEAR) return null;
  if (a[2] < NEAR) a = clipNear(a, b);
  else if (b[2] < NEAR) b = clipNear(b, a);
  return [cam.proj(a), cam.proj(b), (a[2] + b[2]) / 2];
}
function polygon(cam, pts) {
  const inp = pts.map(cam.toCam), out = [];
  for (let i = 0; i < inp.length; i++) {
    const a = inp[i], b = inp[(i + 1) % inp.length];
    const ain = a[2] >= NEAR, bin = b[2] >= NEAR;
    if (ain) out.push(a);
    if (ain !== bin) out.push(clipNear(ain ? a : b, ain ? b : a));
  }
  if (out.length < 3) return null;
  return { pts: out.map(cam.proj), depth: out.reduce((s, p) => s + p[2], 0) / out.length };
}
function fillPoly(ctx, poly, fill, stroke) {
  ctx.beginPath();
  poly.pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
  ctx.closePath();
  if (fill) { ctx.fillStyle = fill; ctx.fill(); }
  if (stroke) { ctx.strokeStyle = stroke; ctx.stroke(); }
}

function bodyToWorld(f, origin) {
  const h = rad(f.heading_deg), p = rad(f.pitch_deg), b = rad(f.bank_deg);
  const sh = Math.sin(h), ch = Math.cos(h), sp = Math.sin(p), cp = Math.cos(p), sb = Math.sin(b), cb = Math.cos(b);
  return ([r, fw, u]) => {
    const r1 = r * cb + u * sb, u1 = u * cb - r * sb;          // roll
    const f2 = fw * cp - u1 * sp, u2 = u1 * cp + fw * sp;       // pitch
    return [origin[0] + r1 * ch + f2 * sh, origin[1] - r1 * sh + f2 * ch, origin[2] + u2]; // heading
  };
}

const PLANE_MESH = [
  { pts: [[0, 1.8, 0], [7, -1, 0], [6.5, -2, 0], [0, -1.2, 0], [-6.5, -2, 0], [-7, -1, 0]], fill: "rgba(79,209,197,0.55)" },
  { pts: [[0, 5, 0.25], [0.8, 0, 0.45], [0, -4.5, 0.4], [-0.8, 0, 0.45]], fill: "rgba(216,226,236,0.85)" },
  { pts: [[0, -3, 0.35], [2.6, -4.6, 0.35], [-2.6, -4.6, 0.35]], fill: "rgba(79,209,197,0.6)" },
  { pts: [[0, -2.8, 0.35], [0, -4.6, 0.35], [0, -4.9, 2.4]], fill: "rgba(246,173,85,0.8)" },
];

function interpFlight() {
  if (!S || !curF) return null;
  const t = clamp((performance.now() - recvAt) / (S.dt * 1000), 0, 1);
  return {
    ...curF,
    x: lerp(prevF.x, curF.x, t), y: lerp(prevF.y, curF.y, t),
    altitude_m: lerp(prevF.altitude_m, curF.altitude_m, t),
    heading_deg: lerpAngle(prevF.heading_deg, curF.heading_deg, t),
    bank_deg: lerp(prevF.bank_deg, curF.bank_deg, t),
    pitch_deg: lerp(prevF.pitch_deg, curF.pitch_deg, t),
  };
}

function drawVR() {
  const [ctx, W, H] = fit($("vr"));
  const f = interpFlight();
  const engine = window.QEAAI_VR;
  if (engine && engine.ready) { // Three.js 엔진이 3D 를 그리고, 이 캔버스는 HUD 오버레이만
    ctx.clearRect(0, 0, W, H);
    if (f) { engine.render(f, S); drawHUD(ctx, W, H, f); }
    return;
  }
  if (!f) { ctx.fillStyle = "#0a1017"; ctx.fillRect(0, 0, W, H); return; }
  const { cam, plane } = makeCamera(f, W, H);

  // 하늘
  const sky = ctx.createLinearGradient(0, 0, 0, H);
  sky.addColorStop(0, "#050a12"); sky.addColorStop(0.55, "#0e2233"); sky.addColorStop(1, "#163449");
  ctx.fillStyle = sky; ctx.fillRect(0, 0, W, H);

  // 지평선 + 지면
  const far = 1e5;
  const A = cam.proj(cam.toCam([cam.x + far * Math.sin(cam.h - 1.2), cam.y + far * Math.cos(cam.h - 1.2), cam.z]));
  const B = cam.proj(cam.toCam([cam.x + far * Math.sin(cam.h + 1.2), cam.y + far * Math.cos(cam.h + 1.2), cam.z]));
  let dx = B[0] - A[0], dy = B[1] - A[1];
  const len = Math.hypot(dx, dy) || 1; dx /= len; dy /= len;
  let nx = -dy, ny = dx;
  const gRef = cam.toCam([cam.x + 100 * Math.sin(cam.h), cam.y + 100 * Math.cos(cam.h), cam.z - 100]);
  const G = gRef[2] > NEAR ? cam.proj(gRef) : [W / 2, H * 2];
  if ((G[0] - A[0]) * nx + (G[1] - A[1]) * ny < 0) { nx = -nx; ny = -ny; }
  const E = 1e4;
  const a1 = [A[0] - dx * E, A[1] - dy * E], b1 = [B[0] + dx * E, B[1] + dy * E];
  const ground = ctx.createLinearGradient(A[0], A[1], A[0] + nx * H, A[1] + ny * H);
  ground.addColorStop(0, "#0d1b22"); ground.addColorStop(1, "#06100f");
  ctx.fillStyle = ground;
  ctx.beginPath();
  ctx.moveTo(...a1); ctx.lineTo(...b1); ctx.lineTo(b1[0] + nx * E, b1[1] + ny * E); ctx.lineTo(a1[0] + nx * E, a1[1] + ny * E);
  ctx.closePath(); ctx.fill();
  ctx.strokeStyle = "rgba(79,209,197,0.35)"; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(...a1); ctx.lineTo(...b1); ctx.stroke();

  // 지면 격자 (거리별 투명도 버킷)
  const SP = 30, R = 540, SEG = 60;
  const gx0 = Math.floor((cam.x - R) / SP) * SP, gy0 = Math.floor((cam.y - R) / SP) * SP;
  const buckets = [[], [], [], [], []];
  const addSeg = (P, Q) => {
    const s = segment(cam, P, Q);
    if (!s) return;
    const dist = Math.hypot((P[0] + Q[0]) / 2 - cam.x, (P[1] + Q[1]) / 2 - cam.y);
    const k = Math.floor((dist / R) * buckets.length);
    if (k < buckets.length) buckets[k].push(s);
  };
  for (let gx = gx0; gx <= cam.x + R; gx += SP)
    for (let y = gy0; y < cam.y + R; y += SEG) addSeg([gx, y, 0], [gx, y + SEG, 0]);
  for (let gy = gy0; gy <= cam.y + R; gy += SP)
    for (let x = gx0; x < cam.x + R; x += SEG) addSeg([x, gy, 0], [x + SEG, gy, 0]);
  buckets.forEach((segs, k) => {
    ctx.strokeStyle = `rgba(79,209,197,${(0.32 * (1 - k / buckets.length)).toFixed(3)})`;
    ctx.beginPath();
    segs.forEach(([p, q]) => { ctx.moveTo(...p); ctx.lineTo(...q); });
    ctx.stroke();
  });

  // 활주로
  const rw = polygon(cam, [[-10, -20, 0], [10, -20, 0], [10, 300, 0], [-10, 300, 0]]);
  if (rw) fillPoly(ctx, rw, "rgba(40,55,70,0.9)", "rgba(216,226,236,0.25)");
  ctx.strokeStyle = "rgba(216,226,236,0.6)"; ctx.lineWidth = 2;
  ctx.beginPath();
  for (let y = 0; y < 290; y += 25) {
    const s = segment(cam, [0, y, 0.05], [0, y + 12, 0.05]);
    if (s) { ctx.moveTo(...s[0]); ctx.lineTo(...s[1]); }
  }
  ctx.stroke(); ctx.lineWidth = 1;

  // IoT 비콘 (디지털 트윈 디바이스)
  const linked = Object.fromEntries(S.twin.filter((d) => d.kind === "iot_beacon").map((d) => [d.id, d.linked]));
  Object.entries(S.beacons).forEach(([id, b]) => {
    const on = linked[id];
    const col = on ? C.ok : "rgba(123,138,153,0.8)";
    ctx.strokeStyle = on ? "rgba(104,211,145,0.35)" : "rgba(123,138,153,0.18)";
    ctx.setLineDash([6, 6]); ctx.beginPath();
    for (let i = 0; i < 48; i++) {
      const a0 = (i / 48) * 2 * Math.PI, a1 = ((i + 1) / 48) * 2 * Math.PI, r = S.beacon_range;
      const s = segment(cam, [b.x + r * Math.cos(a0), b.y + r * Math.sin(a0), 0], [b.x + r * Math.cos(a1), b.y + r * Math.sin(a1), 0]);
      if (s) { ctx.moveTo(...s[0]); ctx.lineTo(...s[1]); }
    }
    ctx.stroke(); ctx.setLineDash([]);
    const mast = segment(cam, [b.x, b.y, 0], [b.x, b.y, 40]);
    if (mast) {
      ctx.strokeStyle = col; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(...mast[0]); ctx.lineTo(...mast[1]); ctx.stroke(); ctx.lineWidth = 1;
      const top = cam.toCam([b.x, b.y, 40]);
      if (top[2] > NEAR) {
        const [sx, sy] = cam.proj(top), r = clamp(600 / top[2], 2, 10);
        ctx.fillStyle = col; ctx.shadowColor = col; ctx.shadowBlur = on ? 14 : 0;
        ctx.beginPath(); ctx.arc(sx, sy, r, 0, 2 * Math.PI); ctx.fill(); ctx.shadowBlur = 0;
        ctx.fillStyle = C.text; ctx.font = "11px 'Pretendard Variable', system-ui"; ctx.fillText(id, sx + r + 4, sy + 4);
      }
    }
  });

  // 비행체 그림자
  if (f.altitude_m > 0.1) {
    const shadowW = bodyToWorld({ ...f, pitch_deg: 0 }, [plane[0], plane[1], 0.02]);
    PLANE_MESH.slice(0, 2).forEach((m) => {
      const poly = polygon(cam, m.pts.map((p) => { const w = shadowW([p[0], p[1], 0]); return [w[0], w[1], 0.02]; }));
      if (poly) fillPoly(ctx, poly, `rgba(0,0,0,${clamp(0.45 - f.altitude_m / 300, 0.08, 0.45)})`);
    });
  }

  // 비행체
  const toW = bodyToWorld(f, plane);
  const polys = PLANE_MESH.map((m) => ({ m, poly: polygon(cam, m.pts.map(toW)) })).filter((x) => x.poly);
  polys.sort((a, b) => b.poly.depth - a.poly.depth);
  polys.forEach(({ m, poly }) => fillPoly(ctx, poly, m.fill, "rgba(255,255,255,0.7)"));
  if (S.flight.throttle > 0.02) { // 엔진 불꽃
    const tail = cam.toCam(toW([0, -5.2, 0.4]));
    if (tail[2] > NEAR) {
      const [sx, sy] = cam.proj(tail), r = (8 + 14 * S.flight.throttle) * (20 / tail[2]);
      const g = ctx.createRadialGradient(sx, sy, 0, sx, sy, r);
      g.addColorStop(0, "rgba(246,173,85,0.9)"); g.addColorStop(1, "rgba(246,173,85,0)");
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(sx, sy, r, 0, 2 * Math.PI); ctx.fill();
    }
  }

  drawHUD(ctx, W, H, f);
}

function drawHUD(ctx, W, H, f) {
  const d = S.decision;
  ctx.font = "12px 'JetBrains Mono', ui-monospace, monospace";
  ctx.textBaseline = "middle";

  // 방위 테이프
  const cx = W / 2, ty = 22, span = 120, pxPerDeg = Math.min(W * 0.5, 420) / span;
  ctx.fillStyle = "rgba(8,12,17,0.55)"; ctx.fillRect(cx - span / 2 * pxPerDeg, 6, span * pxPerDeg, 32);
  ctx.strokeStyle = "rgba(216,226,236,0.6)"; ctx.fillStyle = C.text; ctx.textAlign = "center";
  for (let deg = Math.ceil((f.heading_deg - span / 2) / 10) * 10; deg <= f.heading_deg + span / 2; deg += 10) {
    const x = cx + (deg - f.heading_deg) * pxPerDeg, norm = ((deg % 360) + 360) % 360;
    ctx.beginPath(); ctx.moveTo(x, 30); ctx.lineTo(x, norm % 30 === 0 ? 22 : 26); ctx.stroke();
    if (norm % 30 === 0) ctx.fillText({ 0: "N", 90: "E", 180: "S", 270: "W" }[norm] ?? String(norm), x, 14);
  }
  ctx.fillStyle = C.amber;
  ctx.beginPath(); ctx.moveTo(cx, 38); ctx.lineTo(cx - 5, 44); ctx.lineTo(cx + 5, 44); ctx.closePath(); ctx.fill();

  // 속도 / 고도 박스
  const box = (x, label, value, unit, sub) => {
    ctx.fillStyle = "rgba(8,12,17,0.6)"; ctx.fillRect(x, H / 2 - 30, 96, 60);
    ctx.strokeStyle = "rgba(79,209,197,0.5)"; ctx.strokeRect(x + 0.5, H / 2 - 29.5, 95, 59);
    ctx.textAlign = "left"; ctx.fillStyle = C.muted; ctx.fillText(label, x + 8, H / 2 - 16);
    ctx.fillStyle = C.text; ctx.font = "bold 18px 'JetBrains Mono', ui-monospace, monospace"; ctx.fillText(value, x + 8, H / 2 + 4);
    ctx.font = "11px 'JetBrains Mono', ui-monospace, monospace"; ctx.fillStyle = C.muted; ctx.fillText(sub || unit, x + 8, H / 2 + 20);
    ctx.font = "12px 'JetBrains Mono', ui-monospace, monospace";
  };
  box(12, "SPD", f.speed_mps.toFixed(1), "m/s", "m/s");
  const climb = S.flight.climb_mps;
  box(W - 108, "ALT", f.altitude_m.toFixed(1), "m", `${climb >= 0 ? "▲" : "▼"} ${Math.abs(climb).toFixed(1)} m/s`);

  // 실제 바람 (기상 풍향은 불어오는 방향, 화살표는 불어가는 방향)
  if (S.env && S.env.weather) {
    const w = S.env.weather, bx = W - 108, by = H / 2 + 40;
    ctx.fillStyle = "rgba(8,12,17,0.6)"; ctx.fillRect(bx, by, 96, 44);
    ctx.strokeStyle = "rgba(79,209,197,0.5)"; ctx.strokeRect(bx + 0.5, by + 0.5, 95, 43);
    ctx.fillStyle = C.muted; ctx.textAlign = "left"; ctx.fillText("WIND · REAL", bx + 8, by + 12);
    ctx.fillStyle = C.text; ctx.fillText(`${w.wind_speed_10m.toFixed(1)} m/s`, bx + 8, by + 30);
    const rel = rad(w.wind_direction_10m + 180 - f.heading_deg);
    ctx.save(); ctx.translate(bx + 78, by + 26); ctx.rotate(rel);
    ctx.strokeStyle = C.accent; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(0, 9); ctx.lineTo(0, -9);
    ctx.moveTo(-5, -4); ctx.lineTo(0, -9); ctx.lineTo(5, -4); ctx.stroke(); ctx.restore(); ctx.lineWidth = 1;
  }

  // 스로틀
  const thx = 12, thy = H - 130, thh = 100;
  ctx.fillStyle = "rgba(8,12,17,0.6)"; ctx.fillRect(thx, thy, 22, thh);
  ctx.fillStyle = C.amber; ctx.fillRect(thx + 4, thy + 4 + (thh - 8) * (1 - S.flight.throttle), 14, (thh - 8) * S.flight.throttle);
  ctx.fillStyle = C.muted; ctx.textAlign = "left"; ctx.fillText(`THR ${(S.flight.throttle * 100).toFixed(0)}%`, thx, thy + thh + 12);

  // 상태 표시
  const status = !d ? ["BROWNOUT", C.amber] : d.trusted ? ["CONTROL · " + (LABEL[d.intent] || d.intent), C.ok] : ["LOCKED", C.danger];
  const flight = f.altitude_m > 0 ? ["AIRBORNE", C.accent] : ["GROUND", C.muted];
  ctx.textAlign = "center"; ctx.font = "bold 13px 'JetBrains Mono', ui-monospace, monospace";
  [[status, W / 2 - 80], [flight, W / 2 + 80]].forEach(([[txt, col], x]) => {
    const w = ctx.measureText(txt).width + 20;
    ctx.fillStyle = "rgba(8,12,17,0.65)"; ctx.fillRect(x - w / 2, H - 36, w, 24);
    ctx.strokeStyle = col; ctx.strokeRect(x - w / 2 + 0.5, H - 35.5, w - 1, 23);
    ctx.fillStyle = col; ctx.fillText(txt, x, H - 24);
  });

  // 제어 잠김 오버레이
  if (d && !d.trusted) {
    const g = ctx.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.3, W / 2, H / 2, Math.max(W, H) * 0.7);
    g.addColorStop(0, "rgba(252,92,101,0)"); g.addColorStop(1, "rgba(252,92,101,0.28)");
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = C.danger; ctx.font = "bold 15px 'Pretendard Variable', system-ui";
    ctx.fillText("제어 잠김: 페어링된 사용자가 아닙니다 (fail-safe 감속)", W / 2, 64);
  }
  ctx.textBaseline = "alphabetic"; ctx.textAlign = "left";
}

/* ---------- 미니맵 (트윈 맵) ---------- */
function drawMinimap() {
  const [ctx, W, H] = fit($("minimap"));
  ctx.fillStyle = "rgba(8,12,17,0.82)"; ctx.fillRect(0, 0, W, H);
  if (!S || !curF) return;
  const t = clamp((performance.now() - recvAt) / (S.dt * 1000), 0, 1);
  const px = lerp(prevF.x, curF.x, t), py = lerp(prevF.y, curF.y, t);
  const scale = W / 700; // 700 m 폭
  const tx = (x) => W / 2 + (x - px) * scale, ty = (y) => H / 2 - (y - py) * scale;

  ctx.strokeStyle = "rgba(79,209,197,0.08)";
  for (let g = Math.floor((px - 400) / 100) * 100; g < px + 400; g += 100) { ctx.beginPath(); ctx.moveTo(tx(g), 0); ctx.lineTo(tx(g), H); ctx.stroke(); }
  for (let g = Math.floor((py - 400) / 100) * 100; g < py + 400; g += 100) { ctx.beginPath(); ctx.moveTo(0, ty(g)); ctx.lineTo(W, ty(g)); ctx.stroke(); }

  ctx.fillStyle = "rgba(216,226,236,0.25)";
  ctx.fillRect(tx(-10), ty(300), 20 * scale, 320 * scale);

  const linked = Object.fromEntries(S.twin.filter((d) => d.kind === "iot_beacon").map((d) => [d.id, d.linked]));
  Object.entries(S.beacons).forEach(([id, b]) => {
    ctx.strokeStyle = linked[id] ? "rgba(104,211,145,0.5)" : "rgba(123,138,153,0.25)";
    ctx.beginPath(); ctx.arc(tx(b.x), ty(b.y), S.beacon_range * scale, 0, 2 * Math.PI); ctx.stroke();
    ctx.fillStyle = linked[id] ? C.ok : C.muted;
    ctx.beginPath(); ctx.arc(tx(b.x), ty(b.y), 3, 0, 2 * Math.PI); ctx.fill();
  });

  if (S.trail.length > 1) {
    ctx.strokeStyle = "rgba(246,173,85,0.7)"; ctx.beginPath();
    S.trail.forEach(([x, y], i) => (i ? ctx.lineTo(tx(x), ty(y)) : ctx.moveTo(tx(x), ty(y))));
    ctx.lineTo(tx(px), ty(py)); ctx.stroke();
  }

  const h = rad(lerpAngle(prevF.heading_deg, curF.heading_deg, t));
  ctx.save(); ctx.translate(W / 2, H / 2); ctx.rotate(h);
  ctx.fillStyle = C.accent; ctx.beginPath(); ctx.moveTo(0, -8); ctx.lineTo(5, 6); ctx.lineTo(0, 3); ctx.lineTo(-5, 6); ctx.closePath(); ctx.fill();
  ctx.restore();
  ctx.fillStyle = C.muted; ctx.font = "10px 'Pretendard Variable', system-ui"; ctx.fillText("TWIN MAP · N↑", 6, 13);
}

/* ---------- 전원 ---------- */
function drawSOC() {
  const [ctx, W, H] = fit($("soc"));
  ctx.clearRect(0, 0, W, H);
  if (!S) return;
  const soc = S.power.soc, cx = W / 2, cy = H / 2, r = Math.min(W, H) / 2 - 8;
  const col = soc > 0.5 ? C.ok : soc > 0.2 ? C.amber : C.danger;
  ctx.lineWidth = 9; ctx.lineCap = "round";
  ctx.strokeStyle = C.line; ctx.beginPath(); ctx.arc(cx, cy, r, 0.75 * Math.PI, 2.25 * Math.PI); ctx.stroke();
  ctx.strokeStyle = col; ctx.beginPath(); ctx.arc(cx, cy, r, 0.75 * Math.PI, (0.75 + 1.5 * soc) * Math.PI); ctx.stroke();
  ctx.fillStyle = C.text; ctx.textAlign = "center"; ctx.font = "bold 20px 'JetBrains Mono', ui-monospace, monospace";
  ctx.fillText(`${(soc * 100).toFixed(1)}%`, cx, cy + 4);
  ctx.fillStyle = C.muted; ctx.font = "11px 'Pretendard Variable', system-ui"; ctx.fillText("SOC", cx, cy + 22);
  ctx.textAlign = "left"; ctx.lineCap = "butt";
}

function drawSpark() {
  const [ctx, W, H] = fit($("spark"));
  ctx.clearRect(0, 0, W, H);
  if (!S || S.history.length < 2) return;
  const hist = S.history, n = 150, pad = 6;
  const x = (i) => pad + (i + (n - hist.length)) / (n - 1) * (W - 2 * pad);
  const y = (v) => H - pad - v * (H - 2 * pad);
  ctx.fillStyle = "rgba(246,173,85,0.25)"; ctx.beginPath(); ctx.moveTo(x(0), y(0));
  hist.forEach((h, i) => ctx.lineTo(x(i), y(h.act))); ctx.lineTo(x(hist.length - 1), y(0)); ctx.closePath(); ctx.fill();
  [["trust", C.violet], ["soc", C.accent]].forEach(([k, col]) => {
    ctx.strokeStyle = col; ctx.lineWidth = 1.5; ctx.beginPath();
    hist.forEach((h, i) => (i ? ctx.lineTo(x(i), y(h[k])) : ctx.moveTo(x(i), y(h[k]))));
    ctx.stroke();
  });
  ctx.lineWidth = 1;
}

/* ---------- QE ---------- */
function drawQE() {
  const [ctx, W, H] = fit($("qe"));
  ctx.clearRect(0, 0, W, H);
  if (!info) return;
  const L = 34, Rr = 10, T = 10, B = 24, vmax = 0.4, cmax = 12;
  const x = (v) => L + (v / vmax) * (W - L - Rr), y = (c) => H - B - (c / cmax) * (H - T - B);
  ctx.strokeStyle = C.line; ctx.fillStyle = C.muted; ctx.font = "10px 'JetBrains Mono', ui-monospace, monospace";
  for (let c = 0; c <= cmax; c += 4) { ctx.beginPath(); ctx.moveTo(L, y(c)); ctx.lineTo(W - Rr, y(c)); ctx.stroke(); ctx.fillText(String(c), 8, y(c) + 3); }
  for (let v = 0; v <= vmax + 1e-9; v += 0.1) ctx.fillText(v.toFixed(1), x(v) - 8, H - 8);
  ctx.fillText("V (전극)", W - Rr - 52, H - 8);
  const line = (key, col, dash) => {
    ctx.strokeStyle = col; ctx.setLineDash(dash || []); ctx.lineWidth = 1.6; ctx.beginPath();
    info.qe_curve.forEach((p, i) => (i ? ctx.lineTo(x(p.v), y(p[key])) : ctx.moveTo(x(p.v), y(p[key]))));
    ctx.stroke(); ctx.setLineDash([]); ctx.lineWidth = 1;
  };
  line("cdl", C.muted, [4, 4]); line("cq", C.accent); line("ctot", C.text);
  if (S) {
    const ve = clamp(S.power.cell_v / 2, 0, vmax);
    const pt = info.qe_curve.reduce((a, b) => (Math.abs(b.v - ve) < Math.abs(a.v - ve) ? b : a));
    ctx.strokeStyle = "rgba(246,173,85,0.6)"; ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(x(ve), T); ctx.lineTo(x(ve), H - B); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = C.amber; ctx.beginPath(); ctx.arc(x(ve), y(pt.ctot), 4, 0, 2 * Math.PI); ctx.fill();
    $("qeReadout").textContent =
      `전극 전위 ${ve.toFixed(3)} V · Cq ${pt.cq.toFixed(2)} · Ctotal ${pt.ctot.toFixed(2)} µF/cm² · 양자 제한 ${(pt.ctot / pt.cdl * 100).toFixed(0)}%`;
  }
}

/* ---------- Chart.js 차트 ---------- */
let charts = null;
function qeReadout() {
  const ve = clamp(S.power.cell_v / 2, 0, 0.4);
  const pt = info.qe_curve.reduce((a, b) => (Math.abs(b.v - ve) < Math.abs(a.v - ve) ? b : a));
  $("qeReadout").textContent =
    `전극 전위 ${ve.toFixed(3)} V · Cq ${pt.cq.toFixed(2)} · Ctotal ${pt.ctot.toFixed(2)} µF/cm² · 양자 제한 ${(pt.ctot / pt.cdl * 100).toFixed(0)}%`;
  return { ve, pt };
}

function initCharts() {
  const Chart = window.Chart;
  Chart.defaults.color = C.muted;
  Chart.defaults.borderColor = "rgba(31,44,57,0.9)";
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.font.size = 10;
  Chart.defaults.animation = false;
  const line = (label, color, extra = {}) => ({ label, data: [], borderColor: color, borderWidth: 1.6, pointRadius: 0, tension: 0.25, ...extra });
  const hist = new Chart($("spark"), {
    type: "line",
    data: { labels: [], datasets: [
      line("SOC", C.accent),
      line("근육 활성도", C.amber, { backgroundColor: "rgba(246,173,85,0.18)", fill: true, borderWidth: 1 }),
      line("trust", C.violet),
    ] },
    options: {
      responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: false } },
      scales: { x: { display: false }, y: { min: 0, max: 1, ticks: { stepSize: 0.5 } } },
    },
  });
  const curve = (k) => info.qe_curve.map((p) => ({ x: p.v, y: p[k] }));
  const qe = new Chart($("qe"), {
    type: "line",
    data: { datasets: [
      line("Cq 양자", C.accent, { data: curve("cq") }),
      line("Cdl 이중층", C.muted, { data: curve("cdl"), borderDash: [4, 4], borderWidth: 1.2 }),
      line("Ctotal", C.text, { data: curve("ctot") }),
      { label: "현재 셀", type: "scatter", data: [], backgroundColor: C.amber, borderColor: C.amber, pointRadius: 5 },
    ] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { type: "linear", min: 0, max: 0.4, title: { display: true, text: "전극 전위 (V)" } },
        y: { min: 0, max: 12, title: { display: true, text: "µF/cm²" } },
      },
    },
  });
  charts = { hist, qe };
}

function updateCharts() {
  if (!charts) initCharts();
  const h = S.history;
  const hist = charts.hist;
  hist.data.labels = h.map((p) => p.t);
  hist.data.datasets[0].data = h.map((p) => p.soc);
  hist.data.datasets[1].data = h.map((p) => p.act);
  hist.data.datasets[2].data = h.map((p) => p.trust);
  hist.update("none");
  const { ve, pt } = qeReadout();
  charts.qe.data.datasets[3].data = [{ x: ve, y: pt.ctot }];
  charts.qe.update("none");
}

/* ---------- 입력 ---------- */
const KEYMAP = {
  KeyW: "fist", ArrowUp: "fist", KeyS: "open", ArrowDown: "open",
  KeyA: "wrist_left", ArrowLeft: "wrist_left", KeyD: "wrist_right", ArrowRight: "wrist_right",
};
const held = [];
let lastSent = null;
function sendGesture() {
  const g = held.length ? held[held.length - 1] : "rest";
  document.querySelectorAll("#pad button").forEach((b) => b.classList.toggle("held", held.includes(b.dataset.g)));
  if (g !== lastSent) { lastSent = g; post({ gesture: g }); }
}
function press(g) { if (!held.includes(g)) held.push(g); sendGesture(); }
function release(g) { const i = held.indexOf(g); if (i >= 0) held.splice(i, 1); sendGesture(); }

function bindInput() {
  window.addEventListener("keydown", (e) => {
    const g = KEYMAP[e.code];
    if (!g || e.metaKey || e.ctrlKey || e.altKey) return;
    e.preventDefault();
    if (!e.repeat) press(g);
  });
  window.addEventListener("keyup", (e) => { const g = KEYMAP[e.code]; if (g) release(g); });
  window.addEventListener("blur", () => { held.length = 0; sendGesture(); });

  document.querySelectorAll("#pad button").forEach((b) => {
    const g = b.dataset.g;
    if (g === "rest") { b.addEventListener("click", () => { held.length = 0; lastSent = null; sendGesture(); }); return; }
    b.addEventListener("pointerdown", (e) => { b.setPointerCapture(e.pointerId); press(g); });
    ["pointerup", "pointercancel", "lostpointercapture"].forEach((ev) => b.addEventListener(ev, () => release(g)));
  });
  document.querySelectorAll("#wearerSeg button").forEach((b) => b.addEventListener("click", () => post({ wearer: b.dataset.wearer })));
  document.querySelectorAll("#modeSeg button").forEach((b) => b.addEventListener("click", () => { held.length = 0; lastSent = "rest"; post({ mode: b.dataset.mode }); }));
  $("recharge").addEventListener("click", () => post({ recharge: true }));
  $("reset").addEventListener("click", () => { held.length = 0; lastSent = "rest"; post({ reset: true }); });
}

/* ---------- 메인 루프 ---------- */
function frame() {
  try {
    drawVR();
    drawMinimap();
    drawEMG();
    if (dirty) {
      drawSOC();
      if (window.Chart) { if (info && S) updateCharts(); }
      else { drawSpark(); drawQE(); }
      dirty = false;
    }
  } catch (e) { console.error(e); }
  requestAnimationFrame(frame);
}

window.addEventListener("resize", () => { dirty = true; });
window.addEventListener("qeaai:chartjs", () => { dirty = true; }); // Chart.js 가 늦게 도착하면 그때 전환
// Three.js 엔진(vr3d.js) 이 읽는 공개 인터페이스
window.QEAAI = { getState: () => S, getFlight: interpFlight, getInfo: () => info, post };
setTimeout(() => { // CDN/WebGL 을 쓸 수 없으면 2D 렌더러로 계속
  if (!(window.QEAAI_VR && window.QEAAI_VR.ready)) {
    const b = $("engineBadge");
    if (b) { b.textContent = "2D 폴백 렌더러 (WebGL 또는 CDN 사용 불가)"; b.hidden = false; }
  }
}, 6000);
buildScores();
bindInput();
fetch("/api/info").then((r) => r.json()).then((j) => { info = j; dirty = true; }).catch(console.warn);
connect();
requestAnimationFrame(frame);

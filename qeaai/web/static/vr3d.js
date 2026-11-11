/* QEAAI VR 엔진 - Three.js (WebGL2) 실시간 렌더러.
 *
 * 그래픽 파이프라인 (게임 엔진 / DCC 툴 표준 구성):
 *   - 물리 기반 대기 산란 하늘 (Preetham Sky) + PMREM 환경광 (IBL)
 *   - PBR 재질 (MeshStandard / MeshPhysical: metalness-roughness, clearcoat)
 *   - 방향광 그림자 (PCF soft), 거리 안개
 *   - HDR -> ACES Filmic 톤매핑, sRGB 출력
 *   - 후처리: UnrealBloom (발광체 글로우) -> OutputPass
 *   - 에셋: glTF 2.0 (Blender 등에서 export 한 assets/models/flyer.glb 가 있으면 사용)
 *
 * 좌표: 시뮬레이터 (x=동, y=북, alt) -> Three.js (x, alt, -y), Y-up / 기체 기수 +Z (glTF 규약)
 */
import * as THREE from "three";
import { Sky } from "three/addons/objects/Sky.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

const canvas = document.getElementById("vr3d");
const DEG = Math.PI / 180;
const COL = { accent: 0x4fd1c5, ok: 0x68d391, idle: 0x7b8a99, amber: 0xf6ad55, danger: 0xfc5c65 };
const toWorld = (x, y, alt, out = new THREE.Vector3()) => out.set(x, alt, -y);

/* ---------- 렌더러 ---------- */
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "high-performance" });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 0.5;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(58, 1, 0.3, 14000);
camera.position.set(0, 8, 40);

/* ---------- 하늘 / 태양 / 환경광 ---------- */
const sky = new Sky();
sky.scale.setScalar(12000);
const su = sky.material.uniforms;
su.turbidity.value = 5.5;
su.rayleigh.value = 1.4;
su.mieCoefficient.value = 0.004;
su.mieDirectionalG.value = 0.82;
// 태양 방향은 서버가 계산한 실제 태양 고도/방위로 갱신된다 (applyEnvironment). 초기값: 남남동 24도
const sunDir = new THREE.Vector3().setFromSphericalCoords(1, (90 - 24) * DEG, 25 * DEG);
su.sunPosition.value.copy(sunDir);
scene.add(sky);

const pmrem = new THREE.PMREMGenerator(renderer);
const envScene = new THREE.Scene();
envScene.add(sky.clone());
let envRT = pmrem.fromScene(envScene, 0.02);
scene.environment = envRT.texture;
scene.fog = new THREE.Fog(0xa7b8c9, 900, 6500);
const FOG_DAY = new THREE.Color(0xa7b8c9), FOG_NIGHT = new THREE.Color(0x070b12);

const hemi = new THREE.HemisphereLight(0xd6e6ff, 0x3a4a33, 0.55);
scene.add(hemi);
const sun = new THREE.DirectionalLight(0xfff0d8, 3.2);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
Object.assign(sun.shadow.camera, { left: -45, right: 45, top: 45, bottom: -45, near: 1, far: 800 });
sun.shadow.bias = -0.0004;
sun.shadow.normalBias = 0.03;
scene.add(sun, sun.target);
const moon = new THREE.DirectionalLight(0x9fb4ff, 0); // 야간 달빛 (그림자 없음)
moon.position.set(-300, 500, 200);
scene.add(moon);

/* ---------- 절차적 텍스처 / 노이즈 ---------- */
function hash(x, y) {
  const s = Math.sin(x * 127.1 + y * 311.7) * 43758.5453;
  return s - Math.floor(s);
}
function vnoise(x, y) {
  const xi = Math.floor(x), yi = Math.floor(y), xf = x - xi, yf = y - yi;
  const u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf);
  const a = hash(xi, yi), b = hash(xi + 1, yi), c = hash(xi, yi + 1), d = hash(xi + 1, yi + 1);
  return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
}
const fbm = (x, y) => { let s = 0, a = 0.5, f = 1; for (let i = 0; i < 5; i++) { s += a * vnoise(x * f, y * f); f *= 2; a *= 0.5; } return s; };
const smoothstep = (a, b, x) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };

function canvasTexture(w, h, draw, repeat) {
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  draw(c.getContext("2d"), w, h);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = renderer.capabilities.getMaxAnisotropy();
  if (repeat) { tex.wrapS = tex.wrapT = THREE.RepeatWrapping; tex.repeat.set(repeat, repeat); }
  return tex;
}

/* ---------- 지형 ---------- */
const grassTex = canvasTexture(512, 512, (g, w, h) => {
  g.fillStyle = "#56664a"; g.fillRect(0, 0, w, h);
  for (let i = 0; i < 9000; i++) {
    const x = Math.random() * w, y = Math.random() * h, l = 30 + Math.random() * 25;
    g.fillStyle = `hsla(${80 + Math.random() * 30}, ${20 + Math.random() * 20}%, ${l}%, 0.35)`;
    g.fillRect(x, y, 1 + Math.random() * 3, 1 + Math.random() * 3);
  }
}, 320);

const terrainGeo = new THREE.PlaneGeometry(9000, 9000, 220, 220);
terrainGeo.rotateX(-Math.PI / 2);
{
  const pos = terrainGeo.attributes.position, colors = [];
  const low = new THREE.Color(0x8fa07a), mid = new THREE.Color(0x6f7d5c), high = new THREE.Color(0x9a9282);
  const c = new THREE.Color();
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i), z = pos.getZ(i);
    const d = Math.hypot(x, z + 150);
    const hgt = Math.pow(fbm(x / 900, z / 900), 2.2) * 520 * smoothstep(700, 2200, d);
    pos.setY(i, hgt - 0.02);
    const t = Math.min(1, hgt / 260);
    c.copy(low).lerp(mid, Math.min(1, t * 2)).lerp(high, Math.max(0, t * 2 - 1));
    colors.push(c.r, c.g, c.b);
  }
  terrainGeo.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
  terrainGeo.computeVertexNormals();
}
const terrain = new THREE.Mesh(terrainGeo, new THREE.MeshStandardMaterial({ map: grassTex, vertexColors: true, roughness: 0.96, metalness: 0 }));
terrain.receiveShadow = true;
scene.add(terrain);

/* ---------- 활주로 (sim y -20..320, 폭 24 m) ---------- */
const runwayTex = canvasTexture(256, 3584, (g, w, h) => {
  g.fillStyle = "#2c3034"; g.fillRect(0, 0, w, h);
  for (let i = 0; i < 40000; i++) { g.fillStyle = `rgba(255,255,255,${Math.random() * 0.05})`; g.fillRect(Math.random() * w, Math.random() * h, 2, 2); }
  g.fillStyle = "#e8e8e8";
  g.fillRect(8, 0, 6, h); g.fillRect(w - 14, 0, 6, h);                         // 가장자리선
  for (let y = 300; y < h - 300; y += 160) g.fillRect(w / 2 - 3, y, 6, 90);     // 중심선
  for (let k = 0; k < 8; k++) { const x = 24 + k * 27; g.fillRect(x, h - 120, 14, 100); g.fillRect(x, 20, 14, 100); } // 피아노 키
  g.font = "bold 120px 'Pretendard Variable', sans-serif"; g.textAlign = "center";
  g.fillText("36", w / 2, h - 170);
  g.save(); g.translate(w / 2, 170); g.rotate(Math.PI); g.fillText("18", 0, 0); g.restore();
});
const runway = new THREE.Mesh(
  new THREE.PlaneGeometry(24, 340).rotateX(-Math.PI / 2),
  new THREE.MeshStandardMaterial({ map: runwayTex, roughness: 0.85, metalness: 0.05 }),
);
toWorld(0, 150, 0.04, runway.position);
runway.receiveShadow = true;
scene.add(runway);

{ // 활주로 등화 (발광 -> 블룸)
  const n = 2 * 18;
  const lights = new THREE.InstancedMesh(new THREE.SphereGeometry(0.22, 8, 6), new THREE.MeshBasicMaterial({ color: new THREE.Color(9, 7, 4) }), n);
  const m = new THREE.Matrix4(); let i = 0;
  for (let y = -20; y <= 320; y += 20) for (const x of [-12.6, 12.6]) { if (i < n) lights.setMatrixAt(i++, m.makeTranslation(x, 0.25, -y)); }
  lights.count = i;
  scene.add(lights);
}

/* ---------- 구름 (실제 운량) / 별 ---------- */
const clouds = [];
let cloudMat;
{
  const cloudTex = canvasTexture(256, 128, (g, w, h) => {
    for (let i = 0; i < 26; i++) {
      const x = w * (0.15 + Math.random() * 0.7), y = h * (0.35 + Math.random() * 0.35), r = 18 + Math.random() * 38;
      const grd = g.createRadialGradient(x, y, 0, x, y, r);
      grd.addColorStop(0, "rgba(255,255,255,0.55)"); grd.addColorStop(1, "rgba(255,255,255,0)");
      g.fillStyle = grd; g.beginPath(); g.arc(x, y, r, 0, Math.PI * 2); g.fill();
    }
  });
  cloudMat = new THREE.SpriteMaterial({ map: cloudTex, transparent: true, depthWrite: false, fog: true, opacity: 0.9 });
  for (let i = 0; i < 90; i++) {
    const s = new THREE.Sprite(cloudMat);
    clouds.push(s);
    const a = Math.random() * Math.PI * 2, r = 400 + Math.random() * 3600;
    s.position.set(Math.cos(a) * r, 220 + Math.random() * 260, Math.sin(a) * r - 150);
    const k = 180 + Math.random() * 260;
    s.scale.set(k, k / 2, 1);
    scene.add(s);
  }
}

const stars = (() => {
  const n = 2200, pos = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    const v = new THREE.Vector3().setFromSphericalCoords(9000, Math.acos(Math.random() * 0.98), Math.random() * Math.PI * 2);
    pos.set([v.x, v.y, v.z], i * 3);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  const pts = new THREE.Points(g, new THREE.PointsMaterial({ color: 0xffffff, size: 1.6, sizeAttenuation: false, transparent: true, opacity: 0, fog: false, depthWrite: false }));
  pts.renderOrder = -1;
  scene.add(pts);
  return pts;
})();

/* ---------- 디지털 트윈 레이어 ---------- */
const twinLayer = new THREE.Group();
scene.add(twinLayer);
{
  const grid = new THREE.GridHelper(3000, 60, COL.accent, COL.accent);
  grid.material.transparent = true; grid.material.opacity = 0.12; grid.material.depthWrite = false;
  grid.position.set(0, 0.15, -150);
  twinLayer.add(grid);
}

// 라벨: sizeAttenuation=false 라서 거리와 무관하게 화면에서 같은 크기 (HUD 라벨처럼). 내용 갱신 가능
function makeLabel(lines, accent = "#d8e2ec", width = 0.13) {
  const W = 512, LH = 52;
  const c = document.createElement("canvas");
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true, sizeAttenuation: false }));
  sprite.renderOrder = 10;
  let last = "";
  const set = (ls, col = accent) => {
    const key = ls.join("\n") + col;
    if (key === last) return;
    last = key;
    c.width = W; c.height = LH * ls.length + 20;
    const g = c.getContext("2d");
    g.fillStyle = "rgba(8,12,17,0.78)"; g.beginPath(); g.roundRect(2, 2, W - 4, c.height - 4, 14); g.fill();
    g.fillStyle = col; g.fillRect(2, 14, 6, c.height - 28);
    g.textBaseline = "middle"; g.textAlign = "left";
    ls.forEach((t, i) => {
      g.font = i === 0 ? "600 34px 'JetBrains Mono', monospace" : "28px 'Pretendard Variable', sans-serif";
      g.fillStyle = i === 0 ? col : "#d8e2ec";
      g.fillText(t, 24, 10 + LH * i + LH / 2);
    });
    tex.needsUpdate = true;
    sprite.scale.set(width, width * c.height / W, 1);
  };
  set(lines);
  return { sprite, set };
}
const labelSprite = (text) => makeLabel([text]).sprite;

const beacons = {};
const mastMat = new THREE.MeshStandardMaterial({ color: 0xb8c2cc, metalness: 0.85, roughness: 0.35 });
function makeBeacon(id, b, range) {
  const g = new THREE.Group();
  toWorld(b.x, b.y, 0, g.position);
  const base = new THREE.Mesh(new THREE.CylinderGeometry(3, 3.6, 1.2, 24), new THREE.MeshStandardMaterial({ color: 0x48525c, roughness: 0.7, metalness: 0.3 }));
  base.position.y = 0.6;
  const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.35, 0.8, 40, 12), mastMat);
  mast.position.y = 20.6;
  const dishMat = new THREE.MeshStandardMaterial({ color: 0xdfe6ee, metalness: 0.4, roughness: 0.3, side: THREE.DoubleSide });
  const dish = new THREE.Mesh(new THREE.SphereGeometry(2.2, 24, 12, 0, Math.PI * 2, 0, Math.PI / 3), dishMat);
  dish.rotation.x = Math.PI / 2; dish.position.set(0, 32, 0.6);
  const lampMat = new THREE.MeshBasicMaterial({ color: COL.idle });
  const lamp = new THREE.Mesh(new THREE.SphereGeometry(1.0, 20, 12), lampMat);
  lamp.position.y = 41.4;
  [base, mast, dish].forEach((m) => { m.castShadow = true; m.receiveShadow = true; });
  const label = labelSprite(`${id} · SIM`);
  label.position.y = 47;
  g.add(base, mast, dish, lamp, label);
  scene.add(g);

  const ringMat = new THREE.MeshBasicMaterial({ color: COL.idle, transparent: true, opacity: 0.35, depthWrite: false, side: THREE.DoubleSide });
  const ring = new THREE.Mesh(new THREE.RingGeometry(range - 1.2, range + 1.2, 128).rotateX(-Math.PI / 2), ringMat);
  toWorld(b.x, b.y, 0.3, ring.position);
  twinLayer.add(ring);

  const linkGeo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]);
  const link = new THREE.Line(linkGeo, new THREE.LineDashedMaterial({ color: COL.ok, dashSize: 3, gapSize: 2, transparent: true, opacity: 0.9 }));
  link.visible = false; link.frustumCulled = false;
  twinLayer.add(link);
  beacons[id] = { group: g, lamp, lampMat, ringMat, link, dish, top: new THREE.Vector3(b.x, 41.4, -b.y) };
}

/* ---------- 실제 장치 (REAL) ---------- */
const REAL_COL = "#68d391", STALE_COL = "#7b8a99";
const fmt1 = (v, u = "") => (typeof v === "number" ? `${v.toFixed(1)}${u}` : "—");
const compass8 = (deg) => ["북", "북동", "동", "남동", "남", "남서", "서", "북서"][Math.round(((deg % 360) + 360) % 360 / 45) % 8];
const realThings = {};
let nightFactor = 0;

function windowTexture() {
  return canvasTexture(256, 128, (g, w, h) => {
    g.fillStyle = "#000"; g.fillRect(0, 0, w, h);
    for (let y = 12; y < h - 8; y += 22) for (let x = 10; x < w - 10; x += 20)
      if (Math.random() > 0.35) { g.fillStyle = `hsl(${40 + Math.random() * 15}, 80%, ${55 + Math.random() * 20}%)`; g.fillRect(x, y, 12, 12); }
  });
}

function makeGateway(pos) { // 이 컴퓨터 = 엣지 게이트웨이 = 관제 건물
  const g = new THREE.Group();
  toWorld(pos.x, pos.y, 0, g.position);
  const winTex = windowTexture();
  const bodyMat = new THREE.MeshStandardMaterial({ color: 0x5b6b78, metalness: 0.7, roughness: 0.25, emissive: 0xffffff, emissiveMap: winTex, emissiveIntensity: 0 });
  const body = new THREE.Mesh(new THREE.BoxGeometry(16, 8, 10), bodyMat);
  body.position.y = 4;
  const roof = new THREE.Mesh(new THREE.BoxGeometry(16.6, 0.6, 10.6), darkMat);
  roof.position.y = 8.3;
  const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.2, 9, 8), mastMat);
  mast.position.set(5, 13, 2);
  const tip = new THREE.Mesh(new THREE.SphereGeometry(0.3, 10, 8), new THREE.MeshBasicMaterial({ color: new THREE.Color(10, 0.5, 0.4) }));
  tip.position.set(5, 17.6, 2);
  const stripMat = new THREE.MeshBasicMaterial({ color: new THREE.Color(0.4, 4, 2) });
  const strip = new THREE.Mesh(new THREE.BoxGeometry(16.2, 0.35, 10.2), stripMat);
  strip.position.y = 7.6;
  [body, roof, mast].forEach((m) => { m.castShadow = true; m.receiveShadow = true; });
  const label = makeLabel(["THIS PC · REAL", "게이트웨이"], REAL_COL, 0.16);
  label.sprite.position.y = 22;
  g.add(body, roof, mast, tip, strip, label.sprite);
  scene.add(g);
  // 손목밴드 <-> 게이트웨이 무선 링크
  const link = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]),
    new THREE.LineDashedMaterial({ color: COL.accent, dashSize: 2, gapSize: 1.5, transparent: true, opacity: 0.8 }));
  link.frustumCulled = false;
  twinLayer.add(link);
  return { group: g, bodyMat, stripMat, tip, label, link, top: new THREE.Vector3(g.position.x + 5, 17.6, g.position.z + 2) };
}

function makeWeatherMast(pos) { // 실제 날씨 = 기상 관측 마스트 + 풍향 자루
  const g = new THREE.Group();
  toWorld(pos.x, pos.y, 0, g.position);
  const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.18, 10, 10), mastMat);
  mast.position.y = 5; mast.castShadow = true;
  const sockTex = canvasTexture(64, 256, (c, w, h) => { for (let i = 0; i < 5; i++) { c.fillStyle = i % 2 ? "#f4f4f4" : "#ff7a1a"; c.fillRect(0, (i * h) / 5, w, h / 5); } });
  const sockGeo = new THREE.CylinderGeometry(0.25, 0.6, 3.2, 20, 1, true);
  sockGeo.rotateX(Math.PI / 2); sockGeo.translate(0, 0, 1.6); // 기둥 쪽이 넓은 입구, +Z 로 뻗음
  const sock = new THREE.Mesh(sockGeo, new THREE.MeshStandardMaterial({ map: sockTex, side: THREE.DoubleSide, roughness: 0.8 }));
  sock.castShadow = true;
  const pivot = new THREE.Group();
  pivot.position.y = 9.6;
  pivot.add(sock);
  const vane = new THREE.Mesh(new THREE.SphereGeometry(0.22, 12, 8), new THREE.MeshStandardMaterial({ color: 0xdfe6ee, metalness: 0.6, roughness: 0.3 }));
  vane.position.y = 10.1;
  const label = makeLabel(["WEATHER · REAL", "수신 중…"], REAL_COL, 0.15);
  label.sprite.position.y = 15;
  g.add(mast, pivot, vane, label.sprite);
  scene.add(g);
  return { group: g, pivot, label };
}

function makeMqttThing(id, pos) { // MQTT 로 들어온 실제 장치
  const g = new THREE.Group();
  toWorld(pos.x, pos.y, 0, g.position);
  const pillar = new THREE.Mesh(new THREE.CylinderGeometry(0.7, 0.9, 5, 16), new THREE.MeshStandardMaterial({ color: 0x3b4650, metalness: 0.6, roughness: 0.4 }));
  pillar.position.y = 2.5; pillar.castShadow = true;
  const ringMat = new THREE.MeshBasicMaterial({ color: new THREE.Color(0.4, 4, 2.4) });
  const ring = new THREE.Mesh(new THREE.TorusGeometry(1.1, 0.12, 8, 32).rotateX(Math.PI / 2), ringMat);
  ring.position.y = 5.3;
  const label = makeLabel([`${id} · MQTT`], REAL_COL, 0.14);
  label.sprite.position.y = 9;
  g.add(pillar, ring, label.sprite);
  scene.add(g);
  return { group: g, ring, ringMat, label };
}

function updateRealThings(S, time) {
  const places = S.places || {};
  for (const d of S.twin) {
    if (!d.real || !places[d.id]) continue;
    let t = realThings[d.id];
    if (!t) {
      t = realThings[d.id] = d.kind === "edge_gateway" ? makeGateway(places[d.id])
        : d.kind === "weather_station" ? makeWeatherMast(places[d.id]) : makeMqttThing(d.id, places[d.id]);
      t.kind = d.kind;
    }
    toWorld(places[d.id].x, places[d.id].y, 0, t.group.position);
    const stale = d.age_s !== null && d.age_s > (d.kind === "weather_station" ? 1200 : 15);
    const col = stale ? STALE_COL : REAL_COL;
    if (t.kind === "edge_gateway") {
      const temp = d.cpu_temp_c ?? 50;
      const hot = Math.min(1, Math.max(0, (temp - 50) / 40)); // 50°C 녹색 -> 90°C 적색
      t.stripMat.color.setRGB(0.4 + 6 * hot, 4 * (1 - hot) + 0.4, 2 * (1 - hot));
      t.bodyMat.emissiveIntensity = 1.2 * nightFactor;
      t.tip.visible = time % 1.6 < 0.5;
      t.label.set([`THIS PC · REAL`, `CPU ${fmt1(d.cpu_temp_c, "°C")} · ${fmt1(d.cpu_load_pct, "%")}`,
        `배터리 ${d.battery_pct ?? "—"}% · ${fmt1(d.battery_power_w, " W")}`], col);
      // 밴드 <-> 게이트웨이 링크
      const p = t.link.geometry.attributes.position;
      p.setXYZ(0, t.top.x, t.top.y, t.top.z);
      p.setXYZ(1, flyer.position.x, flyer.position.y, flyer.position.z);
      p.needsUpdate = true;
      t.link.computeLineDistances();
      t.link.geometry.computeBoundingSphere();
    } else if (t.kind === "weather_station") {
      if (typeof d.wind_dir_deg === "number") {
        const to = (d.wind_dir_deg + 180) * DEG; // 풍향 자루는 바람이 불어가는 쪽을 가리킨다
        t.pivot.rotation.set(0, Math.PI - to, 0);
        const sp = d.wind_mps || 0, droop = (1 - Math.min(1, sp / 8)) * 1.1;
        t.pivot.children[0].rotation.x = droop + Math.sin(time * (2 + sp)) * 0.03 * Math.min(1, sp);
      }
      t.label.set([`WEATHER · REAL`, `${d.description ?? ""} ${fmt1(d.temp_c, "°C")} · 습도 ${d.humidity_pct ?? "—"}%`,
        `바람 ${typeof d.wind_dir_deg === "number" ? compass8(d.wind_dir_deg) : ""} ${fmt1(d.wind_mps, " m/s")} · 구름 ${d.cloud_pct ?? "—"}%`], col);
    } else {
      const vals = Object.entries(d).filter(([k, v]) => !["id", "kind", "source", "real", "age_s", "x", "y"].includes(k) && v !== null && typeof v !== "object")
        .slice(0, 2).map(([k, v]) => `${k} ${typeof v === "number" ? (Number.isInteger(v) ? v : v.toFixed(1)) : v}`);
      t.label.set([`${d.id} · MQTT`, ...(vals.length ? [vals.join(" · ")] : []), `${d.kind}${stale ? " · 끊김" : ""}`], col);
      t.ringMat.color.set(stale ? 0x555c63 : 0xffffff).multiply(stale ? new THREE.Color(1, 1, 1) : new THREE.Color(0.4, 4, 2.4));
      t.ring.rotation.z = time;
    }
  }
}

/* ---------- 실제 환경: 태양 / 낮밤 / 구름 ---------- */
let envKey = "";
function applyEnvironment(env) {
  if (!env) return;
  const el = env.sun.elevation_deg, az = env.sun.azimuth_deg;
  const w = env.weather;
  const cloud = w ? w.cloud_cover : 25;
  const key = `${el.toFixed(1)}|${az.toFixed(1)}|${cloud}`;
  if (key === envKey) return;
  envKey = key;
  const e = el * DEG, a = az * DEG; // 동=+x, 북=-z
  sunDir.set(Math.cos(e) * Math.sin(a), Math.sin(e), -Math.cos(e) * Math.cos(a));
  su.sunPosition.value.copy(sunDir);
  const day = smoothstep(-8, 10, el);
  nightFactor = 1 - day;
  const overcast = cloud / 100;
  su.turbidity.value = 3 + 9 * overcast;
  su.rayleigh.value = 0.4 + 1.2 * day;
  sun.intensity = 3.2 * smoothstep(-1, 12, el) * (1 - 0.65 * overcast);
  sun.color.setHSL(0.09, 0.6, 0.55 + 0.4 * smoothstep(0, 30, el)); // 저녁 해는 붉게
  hemi.intensity = 0.14 + 0.45 * day;
  hemi.color.setHSL(0.6, 0.5, 0.35 + 0.5 * day);
  moon.intensity = 1.1 * nightFactor * (1 - 0.6 * overcast);
  renderer.toneMappingExposure = 0.5 + 0.45 * nightFactor;
  scene.fog.color.copy(FOG_DAY).lerp(FOG_NIGHT, nightFactor);
  stars.material.opacity = nightFactor * (1 - 0.85 * overcast);
  const visible = Math.round(clouds.length * Math.min(1, 0.05 + overcast));
  clouds.forEach((c, i) => (c.visible = i < visible));
  cloudMat.color.setRGB(1, 1, 1).lerp(new THREE.Color(0.12, 0.14, 0.18), nightFactor);
  envRT.dispose();
  envRT = pmrem.fromScene(envScene, 0.02);
  scene.environment = envRT.texture;
}

/* ---------- 기체 (절차적 PBR 모델, glTF 로 교체 가능) ---------- */
const flyer = new THREE.Group();
flyer.rotation.order = "YXZ";
scene.add(flyer);

const hullMat = new THREE.MeshPhysicalMaterial({ color: 0xdfe5ec, metalness: 0.55, roughness: 0.32, clearcoat: 0.6, clearcoatRoughness: 0.2 });
const accentMat = new THREE.MeshPhysicalMaterial({ color: 0x2fb5aa, metalness: 0.4, roughness: 0.35, clearcoat: 0.5 });
const darkMat = new THREE.MeshStandardMaterial({ color: 0x2a3036, metalness: 0.8, roughness: 0.45 });
const glassMat = new THREE.MeshPhysicalMaterial({ color: 0x0c2233, metalness: 0.1, roughness: 0.04, clearcoat: 1, envMapIntensity: 2.2, transparent: true, opacity: 0.88 });

function wingShape(pts) {
  const s = new THREE.Shape();
  pts.forEach(([x, z], i) => (i ? s.lineTo(x, z) : s.moveTo(x, z)));
  s.closePath();
  return s;
}
function extrudeFlat(shape, thickness) {
  const g = new THREE.ExtrudeGeometry(shape, { depth: thickness, bevelEnabled: true, bevelThickness: 0.05, bevelSize: 0.05, bevelSegments: 2 });
  g.rotateX(Math.PI / 2); // shape (x, y) -> (x, z), 두께는 아래로
  g.translate(0, thickness / 2, 0);
  return g;
}

const procedural = new THREE.Group();
{
  const profile = [[0, -4.7], [0.32, -4.6], [0.55, -3.6], [0.72, -1.5], [0.78, 0.5], [0.7, 2.4], [0.45, 4.0], [0.12, 5.1], [0, 5.3]]
    .map(([r, y]) => new THREE.Vector2(r, y));
  const fuselage = new THREE.Mesh(new THREE.LatheGeometry(profile, 32).rotateX(Math.PI / 2), hullMat);
  const canopy = new THREE.Mesh(new THREE.SphereGeometry(1, 32, 16), glassMat);
  canopy.scale.set(0.5, 0.42, 1.35); canopy.position.set(0, 0.55, 2.0);

  const wing = new THREE.Mesh(extrudeFlat(wingShape([[0, 1.9], [7.2, -0.7], [7.2, -1.6], [0, -1.3], [-7.2, -1.6], [-7.2, -0.7]]), 0.16), hullMat);
  wing.position.y = -0.05;
  const tipL = new THREE.Mesh(new THREE.BoxGeometry(0.25, 0.32, 1.1), accentMat); tipL.position.set(7.25, 0.02, -1.15);
  const tipR = tipL.clone(); tipR.position.x = -7.25;
  const stab = new THREE.Mesh(extrudeFlat(wingShape([[0, -3.0], [2.8, -4.4], [2.8, -4.9], [0, -4.6], [-2.8, -4.9], [-2.8, -4.4]]), 0.1), hullMat);
  stab.position.y = 0.25;
  const finShape = wingShape([[-2.9, 0], [-4.7, 0], [-5.0, 2.5], [-4.3, 2.6]]);
  const finGeo = new THREE.ExtrudeGeometry(finShape, { depth: 0.12, bevelEnabled: true, bevelThickness: 0.04, bevelSize: 0.04, bevelSegments: 2 });
  finGeo.rotateY(-Math.PI / 2); // shape x -> z(뒤쪽 음수), y 는 위
  finGeo.translate(0.06, 0.3, 0);
  const fin = new THREE.Mesh(finGeo, accentMat);
  const intakeL = new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.38, 2.2, 16, 1, true).rotateX(Math.PI / 2), darkMat);
  intakeL.position.set(0.85, -0.05, 0.2);
  const intakeR = intakeL.clone(); intakeR.position.x = -0.85;
  const nozzle = new THREE.Mesh(new THREE.CylinderGeometry(0.36, 0.3, 0.7, 20, 1, true).rotateX(Math.PI / 2), darkMat);
  nozzle.position.z = -4.85;
  [fuselage, wing, tipL, tipR, stab, fin, intakeL, intakeR, nozzle, canopy].forEach((m) => { m.castShadow = true; m.receiveShadow = true; });
  procedural.add(fuselage, canopy, wing, tipL, tipR, stab, fin, intakeL, intakeR, nozzle);
}
flyer.add(procedural);

// 항법등 (좌 적색 / 우 녹색 / 꼬리 백색 스트로브) - 기수 +Z 이면 좌익은 +X
const navLeft = new THREE.Mesh(new THREE.SphereGeometry(0.16, 10, 8), new THREE.MeshBasicMaterial({ color: new THREE.Color(12, 0.6, 0.6) }));
navLeft.position.set(7.4, 0.05, -0.8);
const navRight = new THREE.Mesh(new THREE.SphereGeometry(0.16, 10, 8), new THREE.MeshBasicMaterial({ color: new THREE.Color(0.6, 12, 1.5) }));
navRight.position.set(-7.4, 0.05, -0.8);
const strobeMat = new THREE.MeshBasicMaterial({ color: new THREE.Color(16, 16, 16) });
const strobe = new THREE.Mesh(new THREE.SphereGeometry(0.14, 10, 8), strobeMat);
strobe.position.set(0, 2.95, -4.9);
flyer.add(navLeft, navRight, strobe);

// 엔진 배기 (가산 블렌딩 + 블룸) 와 배기광
const plumeOuter = new THREE.Mesh(
  new THREE.ConeGeometry(0.42, 3.2, 20, 1, true).rotateX(-Math.PI / 2).translate(0, 0, -1.6),
  new THREE.MeshBasicMaterial({ color: new THREE.Color(5, 2, 0.6), transparent: true, opacity: 0.6, blending: THREE.AdditiveBlending, depthWrite: false }),
);
const plumeCore = new THREE.Mesh(
  new THREE.ConeGeometry(0.22, 1.8, 16, 1, true).rotateX(-Math.PI / 2).translate(0, 0, -0.9),
  new THREE.MeshBasicMaterial({ color: new THREE.Color(4, 8, 9), transparent: true, opacity: 0.9, blending: THREE.AdditiveBlending, depthWrite: false }),
);
const plume = new THREE.Group();
plume.position.z = -5.2;
plume.add(plumeOuter, plumeCore);
const exhaustLight = new THREE.PointLight(0xffa155, 0, 22, 2);
exhaustLight.position.z = -5.6;
flyer.add(plume, exhaustLight);

/* ---------- 궤적 (트윈 레이어) / 날개끝 비행운 ---------- */
function makeTrail(max, color, opacity, layer) {
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(max * 3), 3));
  geo.setDrawRange(0, 0);
  const line = new THREE.Line(geo, new THREE.LineBasicMaterial({ color, transparent: true, opacity }));
  line.frustumCulled = false;
  layer.add(line);
  return { geo, max, n: 0, push(v) {
    const a = this.geo.attributes.position;
    if (this.n === this.max) { a.array.copyWithin(0, 3); this.n--; }
    a.setXYZ(this.n++, v.x, v.y, v.z);
    a.needsUpdate = true;
    this.geo.setDrawRange(0, this.n);
  }, clear() { this.n = 0; this.geo.setDrawRange(0, 0); } };
}
const pathTrail = makeTrail(3000, COL.amber, 0.85, twinLayer);
// 날개끝 와류: 최근 약 5 초만 유지
const tipTrailL = makeTrail(50, 0xffffff, 0.3, scene);
const tipTrailR = makeTrail(50, 0xffffff, 0.3, scene);

/* ---------- 후처리 ---------- */
const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
const bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), 0.7, 0.35, 1.6);
composer.addPass(bloom);
composer.addPass(new OutputPass());

/* ---------- 카메라 모드 ---------- */
const controls = new OrbitControls(camera, canvas);
controls.enabled = false;
controls.enableDamping = true;
controls.maxDistance = 600;
controls.minDistance = 8;
let camMode = "chase";
const camTarget = new THREE.Vector3();
const lastFlyerPos = new THREE.Vector3();
const Q_BACK = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI);

function setCamMode(mode) {
  camMode = mode;
  controls.enabled = mode === "orbit";
  if (mode === "orbit") controls.target.copy(flyer.position);
  document.querySelectorAll("#vrTools [data-cam]").forEach((b) => b.classList.toggle("on", b.dataset.cam === mode));
  canvas.style.cursor = mode === "orbit" ? "grab" : "default";
}

/* ---------- 프레임 ---------- */
const clock = new THREE.Clock();
const tmp = new THREE.Vector3(), tmp2 = new THREE.Vector3(), UP7 = new THREE.Vector3(0, 7, 0);
let lastT = null, trailTimer = 0, modelSource = "절차적 PBR 모델";

function resize() {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  const size = renderer.getSize(new THREE.Vector2());
  if (w && h && (size.x !== w || size.y !== h)) {
    renderer.setSize(w, h, false);
    composer.setSize(w, h);
    bloom.resolution.set(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
}

function render(f, S) {
  resize();
  const dt = Math.min(clock.getDelta(), 0.1);
  const time = clock.elapsedTime;

  if (lastT !== null && S.t < lastT - 1) { pathTrail.clear(); tipTrailL.clear(); tipTrailR.clear(); } // 리셋
  lastT = S.t;

  // 기체 자세
  toWorld(f.x, f.y, f.altitude_m + 1.25, flyer.position);
  flyer.rotation.set(-f.pitch_deg * DEG, Math.PI - f.heading_deg * DEG, f.bank_deg * DEG);
  flyer.updateMatrixWorld();

  // 엔진
  const thr = S.flight.throttle;
  const flicker = 0.9 + 0.1 * Math.sin(time * 60) * Math.sin(time * 23);
  plume.visible = thr > 0.02;
  plume.scale.set(0.6 + 0.4 * thr, 0.6 + 0.4 * thr, (0.35 + 1.2 * thr) * flicker);
  exhaustLight.intensity = 45 * thr * flicker;
  strobe.visible = (time % 1.2) < 0.08;

  // 비콘 / 데이터 링크
  const linked = Object.fromEntries(S.twin.filter((d) => d.kind === "iot_beacon").map((d) => [d.id, d.linked]));
  if (!Object.keys(beacons).length) Object.entries(S.beacons).forEach(([id, b]) => makeBeacon(id, b, S.beacon_range));
  Object.entries(beacons).forEach(([id, b]) => {
    const on = !!linked[id];
    b.lampMat.color.set(on ? COL.ok : COL.idle);
    b.lampMat.color.multiplyScalar(on ? 7 + 2 * Math.sin(time * 6) : 1.5);
    b.ringMat.color.set(on ? COL.ok : COL.idle);
    b.ringMat.opacity = on ? 0.55 : 0.25;
    b.dish.rotation.z = time * 0.6;
    b.link.visible = on;
    if (on) {
      const p = b.link.geometry.attributes.position;
      p.setXYZ(0, b.top.x, b.top.y, b.top.z);
      p.setXYZ(1, flyer.position.x, flyer.position.y, flyer.position.z);
      p.needsUpdate = true;
      b.link.computeLineDistances();
      b.link.geometry.computeBoundingSphere();
    }
  });

  // 실제 환경 / 실제 장치
  applyEnvironment(S.env);
  updateRealThings(S, time);

  // 궤적
  trailTimer += dt;
  if (trailTimer > 0.1 && f.speed_mps > 0.2) {
    trailTimer = 0;
    pathTrail.push(flyer.position);
    if (f.altitude_m > 3 && f.speed_mps > 9) {
      tipTrailL.push(flyer.localToWorld(tmp.set(7.4, 0.05, -1.2)));
      tipTrailR.push(flyer.localToWorld(tmp.set(-7.4, 0.05, -1.2)));
    }
  }

  // 그림자 카메라를 기체에 붙인다
  sun.position.copy(flyer.position).addScaledVector(sunDir.y > 0.02 ? sunDir : tmp.set(0, 1, 0), 300);
  sun.target.position.copy(flyer.position);

  // 카메라
  const h = f.heading_deg * DEG;
  if (camMode === "chase") {
    tmp.set(Math.sin(h), 0, -Math.cos(h));
    tmp2.copy(flyer.position).addScaledVector(tmp, -26).add(UP7);
    tmp2.y = Math.max(tmp2.y, 2.5);
    const k = 1 - Math.exp(-dt * 4);
    camera.position.lerp(tmp2, k);
    camTarget.lerp(tmp.multiplyScalar(12).add(flyer.position), k);
    camera.up.set(0, 1, 0);
    camera.lookAt(camTarget);
  } else if (camMode === "cockpit") {
    camera.position.copy(flyer.localToWorld(tmp.set(0, 0.95, 1.6)));
    camera.quaternion.copy(flyer.quaternion).multiply(Q_BACK);
  } else {
    camera.position.add(tmp.copy(flyer.position).sub(lastFlyerPos));
    controls.target.copy(flyer.position);
    controls.update();
  }
  lastFlyerPos.copy(flyer.position);
  applyVisibility();

  composer.render();
}

function applyVisibility() {
  // 조종석 시점에서는 기체 외피를 숨겨 시야를 확보한다
  const inside = camMode === "cockpit";
  procedural.visible = !inside && !gltfRoot;
  if (gltfRoot) gltfRoot.visible = !inside;
}

/* ---------- glTF 에셋 ---------- */
let gltfRoot = null;
const info = await fetch("/api/info").then((r) => r.json()).catch(() => ({}));
if (info.model_url) {
  new GLTFLoader().load(info.model_url, (gltf) => {
    gltfRoot = gltf.scene;
    gltfRoot.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
    flyer.add(gltfRoot);
    procedural.visible = false;
    modelSource = `glTF: ${info.model_url.split("/").pop()}`;
    updateBadge();
  }, undefined, (e) => console.warn("glTF load failed, using procedural model", e));
}

/* ---------- 툴바 ---------- */
function updateBadge() {
  const gl2 = renderer.capabilities.isWebGL2 ? "WebGL2" : "WebGL1";
  const badge = document.getElementById("engineBadge");
  badge.textContent = `Three.js r${THREE.REVISION} · ${gl2} · PBR · ACES · Bloom · ${modelSource}`;
  badge.hidden = false;
}
document.querySelectorAll("#vrTools [data-cam]").forEach((b) => b.addEventListener("click", () => setCamMode(b.dataset.cam)));
const twinBtn = document.getElementById("twinToggle");
twinBtn.addEventListener("click", () => { twinLayer.visible = !twinLayer.visible; twinBtn.classList.toggle("on", twinLayer.visible); });
document.getElementById("vrTools").hidden = false;
canvas.hidden = false;
setCamMode("chase");
updateBadge();

window.QEAAI_VR = { ready: true, render, renderer, scene, camera, setCamMode };

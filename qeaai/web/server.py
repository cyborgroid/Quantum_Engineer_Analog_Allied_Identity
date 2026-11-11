"""QEAAI 시각화 서버 - OS / VR / Game 을 브라우저 화면으로.

표준 라이브러리만 사용한다.
    GET  /              대시보드 (static/index.html)
    GET  /api/info      셀 사양 + 양자 커패시턴스 곡선
    GET  /api/stream    Server-Sent Events, 틱마다 전체 상태
    POST /api/control   {"gesture": "fist"} | {"mode": "demo"|"manual"} |
                        {"wearer": "owner"|"impostor"} | {"recharge": true} | {"reset": true}
    GET  /api/things                       W3C WoT Thing Description 목록 (디지털 트윈 표준)
    GET  /api/things/<id>                  장치 하나의 TD
    GET  /api/things/<id>/properties[/<p>] 현재 속성 값

실제 세계 연결(qeaai.iot.realworld)이 있으면 실측 센서·실제 날씨·MQTT 장치가 트윈에 들어온다.
"""

from __future__ import annotations

import dataclasses
import json
import math
import threading
import zlib
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..agent.allied_agent import AlliedAgent
from ..bio.emg import CHANNELS, GESTURES, EMGSimulator, UserPhysiology
from ..design.band import BandDesign
from ..materials.snss_material import material_report
from ..iot.realworld import MAX_THINGS, RealWorld, parse_thing_message
from ..iot.things import thing_description
from ..materials.quantum import capacitance_breakdown
from ..system.kernel import QEAAIKernel

STATIC_DIR = Path(__file__).parent / "static"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".bin": "application/octet-stream",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".ktx2": "image/ktx2",
}
MODEL_PATH = "assets/models/flyer.glb"  # Blender 등에서 export 한 glTF 2.0 기체 모델 (선택)

# 웹 자동 데모: 이륙 -> 순항 -> 우/좌 선회 -> 감속 -> 착륙 -> 정지 (약 50 초 반복)
WEB_DEMO_LOOP: list[tuple[str, float]] = [
    ("rest", 2.0),
    ("fist", 2.4),
    ("rest", 3.6),
    ("open", 1.4),
    ("rest", 3.0),
    ("wrist_right", 1.6),
    ("rest", 5.0),
    ("wrist_left", 1.6),
    ("rest", 5.0),
    ("wrist_right", 1.2),
    ("rest", 4.0),
    ("open", 1.0),
    ("rest", 18.0),
]

# 가상 IoT 비콘 (디지털 트윈에 등록되는 실물 디바이스 역할)
BEACONS: dict[str, tuple[float, float]] = {
    "beacon-n": (0.0, 260.0),
    "beacon-e": (220.0, 120.0),
    "beacon-w": (-200.0, 160.0),
    "beacon-ne": (180.0, 380.0),
}
BEACON_RANGE_M = 150.0
LABEL_KO = {"rest": "휴식", "fist": "주먹", "open": "펴기", "wrist_left": "손목 좌", "wrist_right": "손목 우"}
EMG_POINTS_PER_WINDOW = 50
AUTO_RECHARGE_SOC = 0.10

# 실제 장치들의 VR 배치 (시뮬레이터 좌표, m)
GATEWAY_ID, WEATHER_ID = "edge-gateway", "weather"
FIXED_PLACES = {GATEWAY_ID: (-45.0, 40.0), WEATHER_ID: (32.0, -8.0)}
MQTT_RING_CENTER, MQTT_RING_RADIUS = (-110.0, 70.0), 38.0
RESERVED_IDS = {"band", "flyer", GATEWAY_ID, WEATHER_ID, *BEACONS}
SKIN_TEMP_C = 33.0  # 손목 피부 온도: 밴드 전해질 온도 = 피부와 외기의 혼합


class Simulation:
    def __init__(self, seed: int = 0, realworld: RealWorld | None = None, persona_path: str | Path | None = None):
        self.seed = seed
        self.realworld = realworld
        self.agent = AlliedAgent(command=self._agent_command, persona_path=persona_path)
        self.cond = threading.Condition()
        self.running = False
        self._thread: threading.Thread | None = None
        self.external: dict[str, dict] = {}  # MQTT 로 들어온 실제 장치 (리셋해도 유지)
        self.places: dict[str, tuple[float, float]] = dict(FIXED_PLACES)
        if realworld and realworld.broker:
            realworld.broker.on_publish = self.ingest_mqtt
        self._build()

    # --- 구성 -------------------------------------------------------------
    def _build(self) -> None:
        self.kernel = QEAAIKernel.build_default(seed=self.seed)
        self.owner_source = self.kernel.source
        impostor = UserPhysiology.random("impostor", self.seed + 999)
        impostor = dataclasses.replace(impostor, noise_floor_mv=self.owner_source.physiology.noise_floor_mv)
        self.impostor_source = EMGSimulator(impostor, seed=self.seed + 3)
        self.wearer = "owner"
        self.mode = "demo"
        self.manual_gesture = "rest"
        self.script_index = 0
        self.script_elapsed = 0.0
        self.x = 0.0
        self.y = 0.0
        self.trail: deque[tuple[float, float]] = deque(maxlen=900)
        self.history: deque[dict] = deque(maxlen=150)
        self.payload: str | None = None
        self.version = getattr(self, "version", 0)
        self.ticks = 0
        self.env_snapshot: dict | None = None
        self.agent.saver, self.agent.locked = False, False  # 새 커널은 기본 부하로 시작 (야간 상태는 유지)
        self._wire_realworld()

    # --- 실제 세계 ----------------------------------------------------------
    def _wire_realworld(self) -> None:
        rw = self.realworld
        bus = self.kernel.bus
        if rw and rw.broker:
            broker = rw.broker

            def bridge(msg) -> None:  # 트윈 -> 바깥 MQTT (retained)
                if msg.topic.startswith("device/"):
                    broker.publish(f"qeaai/twin/{msg.topic.split('/')[1]}/state", msg.payload, retain=True)
                else:
                    broker.publish(f"qeaai/{msg.topic}", msg.payload, retain=True)

            bus.subscribe("device/*/state", bridge)
            bus.subscribe("neural/intent", bridge)
        for tid, thing in self.external.items():  # 리셋 후에도 실제 장치는 트윈에 남는다
            bus.publish(f"device/{tid}/state", {"kind": thing["kind"], "source": "mqtt", **thing["state"]}, sender="mqtt-cache")
        self._publish_real(force=True)

    def _publish_real(self, force: bool = False) -> None:
        rw = self.realworld
        if not rw or (not force and self.ticks % 5):  # 1 초마다
            return
        bus = self.kernel.bus
        gw = rw.gateway_state()
        if gw:
            bus.publish(f"device/{GATEWAY_ID}/state", gw, sender="sysfs")
        weather = rw.weather_state()
        if weather:
            bus.publish(f"device/{WEATHER_ID}/state", weather, sender="open-meteo")
            if weather.get("temp_c") is not None:  # 실제 기온 -> SNSS 전해질 온도 -> 이온전도도/ESR/양자 커패시턴스
                cell = self.kernel.power.pack.cell
                t = 0.6 * SKIN_TEMP_C + 0.4 * float(weather["temp_c"])
                if abs(cell.electrolyte.temperature_c - t) > 0.05:
                    cell.electrolyte = dataclasses.replace(cell.electrolyte, temperature_c=round(t, 2))
        self.env_snapshot = rw.env.snapshot()

    def _agent_command(self, device: str, props: dict) -> None:
        """에이전트 -> 실제 장치 (MQTT 표준 명령 토픽). cond 를 잡은 상태에서 호출된다."""
        rw = self.realworld
        if rw and rw.broker:
            rw.broker.publish(f"qeaai/things/{device}/set", props)
        self.kernel.bus.publish("agent/command", {"device": device, "set": props}, sender="allied_agent")

    def agent_command(self, device: str, props: dict, reason: str) -> dict:
        from ..iot.realworld import THING_ID
        if not isinstance(device, str) or not THING_ID.match(device) or not isinstance(props, dict) or not props:
            raise ValueError("device 는 장치 id, set 은 비어 있지 않은 객체")
        with self.cond:
            self.agent.send(device, props, str(reason)[:60], "external")
        rw = self.realworld
        return {"ok": True, "topic": f"qeaai/things/{device}/set", "delivered_via_mqtt": bool(rw and rw.broker)}

    def ingest_mqtt(self, topic: str, payload: bytes, client_id: str) -> None:
        """MQTT 브로커 스레드에서 호출: 실제 장치 데이터 -> 트윈."""
        parsed = parse_thing_message(topic, payload)
        if not parsed:
            return
        tid, props = parsed
        if tid in RESERVED_IDS:
            return
        with self.cond:
            if tid not in self.external and len(self.external) >= MAX_THINGS:
                return
            thing = self.external.setdefault(tid, {"kind": "mqtt_device", "state": {}})
            if isinstance(props.get("kind"), str):
                thing["kind"] = props.pop("kind")[:32]
            props.pop("kind", None)
            thing["state"].update(props)
            x, y = thing["state"].get("x"), thing["state"].get("y")
            if isinstance(x, (int, float)) and isinstance(y, (int, float)) and not isinstance(x, bool):
                self.places[tid] = (float(x), float(y))
            elif tid not in self.places:
                a = (zlib.crc32(tid.encode()) % 360) * math.pi / 180
                self.places[tid] = (MQTT_RING_CENTER[0] + MQTT_RING_RADIUS * math.cos(a),
                                    MQTT_RING_CENTER[1] + MQTT_RING_RADIUS * math.sin(a))
            self.kernel.bus.publish(f"device/{tid}/state", {"kind": thing["kind"], "source": "mqtt", **props},
                                    sender=f"mqtt:{client_id}")

    def things(self, base_url: str) -> list[dict]:
        rw = self.realworld
        mqtt_url = rw.broker.url if rw and rw.broker else None
        with self.cond:
            scene = self.kernel.twin.scene()
        return [thing_description(d, base_url, mqtt_url) for d in scene]

    def thing_state(self, tid: str) -> dict | None:
        with self.cond:
            return next((d for d in self.kernel.twin.scene() if d["id"] == tid), None)

    # --- 제어 -------------------------------------------------------------
    def control(self, cmd: dict) -> dict:
        with self.cond:
            if cmd.get("reset"):
                self._build()
            if "mode" in cmd:
                if cmd["mode"] not in ("demo", "manual"):
                    raise ValueError("mode must be demo|manual")
                self.mode = cmd["mode"]
                self.manual_gesture = "rest"
                self.script_index, self.script_elapsed = 0, 0.0
            if "gesture" in cmd:
                g = cmd["gesture"]
                if g not in GESTURES:
                    raise ValueError(f"unknown gesture {g!r}")
                self.manual_gesture = g
                if g != "rest":
                    self.mode = "manual"  # 직접 조작하면 자동 데모를 멈춘다
            if "wearer" in cmd:
                w = cmd["wearer"]
                if w not in ("owner", "impostor"):
                    raise ValueError("wearer must be owner|impostor")
                if w != self.wearer:
                    self.wearer = w
                    self.kernel.source = self.owner_source if w == "owner" else self.impostor_source
                    self.kernel.identity.on_worn()  # 밴드를 다른 사람이 착용
            if cmd.get("recharge"):
                cell = self.kernel.power.pack.cell
                cell.voltage = cell.max_voltage
            return {"mode": self.mode, "wearer": self.wearer, "gesture": self.manual_gesture}

    # --- 루프 -------------------------------------------------------------
    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, name="qeaai-kernel", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.running = False

    def _loop(self) -> None:
        dt = self.kernel.dt
        next_t = time.monotonic()
        while self.running:
            with self.cond:
                self.step()
                self.cond.notify_all()
            next_t += dt
            delay = next_t - time.monotonic()
            if delay < -1.0:  # 너무 밀렸으면 따라잡지 않고 리셋
                next_t = time.monotonic()
            time.sleep(max(0.0, delay))

    def _current_gesture(self) -> tuple[str, str]:
        if self.mode == "manual":
            return self.manual_gesture, "MANUAL"
        index = self.script_index
        gesture, seconds = WEB_DEMO_LOOP[index]
        self.script_elapsed += self.kernel.dt
        remaining = max(0.0, seconds - self.script_elapsed)
        if remaining <= 1e-9:
            self.script_index = (index + 1) % len(WEB_DEMO_LOOP)
            self.script_elapsed = 0.0
            if self.script_index == 0:
                self._return_to_base()
        return gesture, f"AUTO {index + 1}/{len(WEB_DEMO_LOOP)} · {LABEL_KO.get(gesture, gesture)} · {remaining:3.1f}s"

    def _return_to_base(self) -> None:
        """자동 데모 한 바퀴가 끝나면 활주로 시작점으로 (실제 장치가 있는 비행장 주변에서 반복)."""
        g = self.kernel.game.state
        if g.airborne:
            return
        self.x, self.y = 0.0, 0.0
        g.heading_deg, g.speed_mps, g.bank_deg, g.throttle = 0.0, 0.0, 0.0, 0.0
        self.trail.clear()

    def step(self) -> dict:
        """한 틱 진행하고 브라우저로 보낼 상태를 만든다 (cond 를 잡은 상태에서 호출)."""
        k = self.kernel
        dt = k.dt
        gesture, script_label = self._current_gesture()
        # 자동 데모는 계속 켜 둘 수 있도록 방전 직전에 충전 (실제 근육 발전은 uW 수준이라 충전이 안 됨)
        if self.mode == "demo" and k.power.pack.state_of_charge < AUTO_RECHARGE_SOC:
            k.power.pack.cell.voltage = k.power.pack.cell.max_voltage
            self.notice_ticks = 15
        if getattr(self, "notice_ticks", 0) > 0:
            self.notice_ticks -= 1
            script_label += " · ⚡ 자동 충전"
        prev_alt = k.game.state.altitude_m
        frame = k.tick(gesture)
        f = frame.flight

        h = math.radians(f.heading_deg)
        self.x += f.speed_mps * math.sin(h) * dt
        self.y += f.speed_mps * math.cos(h) * dt
        wind = self.realworld.env.wind_vector() if self.realworld else (0.0, 0.0)
        if f.airborne:  # 실제 바람이 기체를 밀어낸다 (대지속도 = 대기속도 + 풍속)
            self.x += wind[0] * dt
            self.y += wind[1] * dt
        self.ticks += 1
        self._publish_real()
        climb = (f.altitude_m - prev_alt) / dt
        pitch = math.degrees(math.atan2(climb, max(f.speed_mps, 1.0))) if f.airborne or climb else 0.0
        if f.speed_mps > 0.1:
            self.trail.append((round(self.x, 1), round(self.y, 1)))

        for bid, (bx, by) in BEACONS.items():
            dist = math.hypot(bx - self.x, by - self.y)
            k.bus.publish(
                f"device/{bid}/state",
                {"kind": "iot_beacon", "x": bx, "y": by, "dist_m": round(dist, 1), "linked": dist <= BEACON_RANGE_M},
                sender=bid,
            )

        d = frame.decision
        p = frame.power
        self.history.append({
            "t": round(frame.t, 1),
            "soc": p.state_of_charge,
            "harvest_uw": p.harvest_w * 1e6,
            "act": frame.activation,
            "alt": f.altitude_m,
            "trust": d.trust if d else 0.0,
        })

        stride = max(1, len(k.last_window[0]) // EMG_POINTS_PER_WINDOW) if k.last_window else 1
        state = {
            "t": round(frame.t, 2),
            "dt": dt,
            "mode": self.mode,
            "script": script_label,
            "wearer": self.wearer,
            "owner": k.identity.paired_user,
            "gesture": gesture,
            "activation": frame.activation,
            "emg": [[round(v, 4) for v in ch[::stride]] for ch in k.last_window],
            "channels": list(CHANNELS),
            "decision": None if d is None else {
                "intent": d.intent,
                "confidence": d.confidence,
                "distance": d.distance,
                "threshold": k.identity.auth_threshold,
                "authenticated": d.authenticated,
                "trust": d.trust,
                "trust_threshold": k.identity.TRUST_THRESHOLD,
                "trusted": d.trusted,
                "scores": d.scores,
            },
            "power": {
                "pack_v": p.pack_voltage,
                "pack_max_v": k.power.pack.max_voltage,
                "cell_v": k.power.pack.cell.voltage,
                "soc": p.state_of_charge,
                "energy_j": k.power.pack.stored_energy,
                "load_mw": p.load_w * 1e3,
                "harvest_uw": p.harvest_w * 1e6,
                "current_ma": p.pack_current_a * 1e3,
                "brownout": p.brownout,
                "runtime_s": k.power.runtime_estimate_s(),
            },
            "flight": {
                **f.as_dict(),
                "pitch_deg": pitch,
                "climb_mps": climb,
                "x": self.x,
                "y": self.y,
            },
            "trail": list(self.trail)[::3],
            "beacons": {bid: {"x": bx, "y": by} for bid, (bx, by) in BEACONS.items()},
            "beacon_range": BEACON_RANGE_M,
            "places": {tid: {"x": px, "y": py} for tid, (px, py) in self.places.items()},
            "wind": {"east": wind[0], "north": wind[1]},
            "env": self.env_snapshot,
            "mqtt": self.realworld.status() if self.realworld else {"enabled": False},
            "electrolyte_c": k.power.pack.cell.electrolyte.temperature_c,
            "twin": k.twin.scene(),
            "network": {
                "delivered": k.bus.delivered,
                "rejected": k.bus.rejected,
                "recent": [
                    {"seq": m.seq, "topic": m.topic, "sender": m.sender, "sig": m.signature[:12]}
                    for m in list(k.bus.history)[-8:]
                ],
            },
            "history": list(self.history),
        }
        self.agent.step(state, k, dt)  # Allied Identity 에이전트가 직접 판단하고 제어
        state["agent"] = self.agent.export()
        self.payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        self.version += 1
        return state

    def wait_payload(self, last_version: int, timeout: float = 5.0) -> tuple[int, str | None]:
        with self.cond:
            self.cond.wait_for(lambda: self.version != last_version, timeout=timeout)
            return self.version, self.payload

    # --- 정적 정보 --------------------------------------------------------
    def info(self) -> dict:
        cell = self.kernel.power.pack.cell
        pack = self.kernel.power.pack
        curve = []
        for i in range(0, 41):
            v = i * 0.01
            b = capacitance_breakdown(v, cell.electrode.edl_capacitance_f_per_m2, cell.electrolyte.temperature_k)
            curve.append({"v": v, "cq": b["c_quantum"] * 100, "cdl": b["c_edl"] * 100, "ctot": b["c_total"] * 100})
        return {
            "cell": {
                "electrode_area_m2": cell.electrode.area_m2,
                "conductivity_s_m": cell.electrolyte.conductivity,
                "esr_ohm": cell.esr,
                "c_min_f": cell.capacitance(0.0),
                "c_max_f": cell.capacitance(cell.max_voltage),
                "max_v": cell.max_voltage,
            },
            "pack": {"series": pack.cells_in_series, "parallel": pack.strings_in_parallel,
                     "full_energy_j": pack.full_energy, "esr_ohm": pack.esr},
            "gestures": list(GESTURES),
            "model_url": MODEL_PATH if (STATIC_DIR / MODEL_PATH).is_file() else None,
            "qe_curve": curve,
        }


def make_handler(sim: Simulation) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "QEAAI/0.1"

        def log_message(self, fmt: str, *args) -> None:  # 조용히
            pass

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj: dict, status: int = HTTPStatus.OK) -> None:
            self._send(status, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/api/stream":
                return self._stream()
            if path == "/api/info":
                return self._json(sim.info())
            if path == "/api/agent":
                with sim.cond:
                    return self._json(sim.agent.export())
            if path.startswith("/api/design/"):
                return self._design(path)
            if path == "/api/things" or path.startswith("/api/things/"):
                return self._things(path)
            if path == "/api/state":
                _, payload = sim.wait_payload(-1, timeout=0)
                return self._send(HTTPStatus.OK, (payload or "{}").encode(), "application/json; charset=utf-8")
            name = "index.html" if path in ("/", "") else path.lstrip("/")
            file = (STATIC_DIR / name).resolve()
            if STATIC_DIR.resolve() not in file.parents or not file.is_file():
                return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            self._send(HTTPStatus.OK, file.read_bytes(), CONTENT_TYPES.get(file.suffix, "application/octet-stream"))

        def _design(self, path: str) -> None:
            from urllib.parse import parse_qs, urlparse
            q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            try:
                band = BandDesign(wrist_mm=float(q.get("wrist", 165)), width_mm=float(q.get("width", 32)),
                                  cells=int(q.get("cells", 5)))
            except ValueError as e:
                return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            name = path.rsplit("/", 1)[-1]
            if name == "band.svg":
                return self._send(HTTPStatus.OK, band.svg_template().encode(), "image/svg+xml; charset=utf-8")
            if name == "tattoo.svg":
                return self._send(HTTPStatus.OK, band.svg_tattoo().encode(), "image/svg+xml; charset=utf-8")
            if name == "band.json":
                return self._json({"design": band.metrics(), "material": material_report(), "electrodes": band.electrodes})
            return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def _things(self, path: str) -> None:
            host = self.headers.get("Host") or f"{self.server.server_address[0]}:{self.server.server_address[1]}"
            base = f"http://{host}"
            parts = [p for p in path.split("/") if p][2:]  # api, things 제외
            tds = sim.things(base)
            if not parts:
                return self._json(tds)
            tid = parts[0]
            td = next((t for t in tds if t["id"] == f"urn:qeaai:thing:{tid}"), None)
            state = sim.thing_state(tid)
            if td is None or state is None:
                return self._json({"error": f"no thing {tid!r}"}, HTTPStatus.NOT_FOUND)
            if len(parts) == 1:
                return self._json(td)
            if parts[1] == "properties":
                props = {k: v for k, v in state.items() if k in td["properties"]}
                if len(parts) == 2:
                    return self._json(props)
                if len(parts) == 3 and parts[2] in props:
                    return self._json(props[parts[2]])
            return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            if self.path not in ("/api/control", "/api/agent/command"):
                return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            try:
                length = int(self.headers.get("Content-Length", "0"))
                cmd = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(cmd, dict):
                    raise ValueError("body must be a JSON object")
                if self.path == "/api/agent/command":
                    return self._json(sim.agent_command(cmd.get("device"), cmd.get("set"), cmd.get("reason", "external")))
                self._json(sim.control(cmd))
            except (ValueError, json.JSONDecodeError) as e:
                self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)

        def _stream(self) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            last = -1
            try:
                while sim.running:
                    last, payload = sim.wait_payload(last)
                    if payload is None:
                        self.wfile.write(b": keepalive\n\n")
                    else:
                        self.wfile.write(f"data: {payload}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8765, seed: int = 0, port_tries: int = 10,
          realworld: RealWorld | None = None) -> None:
    if realworld:
        realworld.start()
    persona = Path(__file__).resolve().parents[2] / "data" / "allied_persona.json"
    sim = Simulation(seed=seed, realworld=realworld, persona_path=persona)
    handler = make_handler(sim)
    httpd = None
    for p in range(port, port + port_tries):
        try:
            httpd = ThreadingHTTPServer((host, p), handler)
            break
        except OSError:
            continue
    if httpd is None:
        raise SystemExit(f"포트 {port}-{port + port_tries - 1} 를 모두 사용할 수 없습니다")
    httpd.daemon_threads = True
    sim.start()
    url = f"http://{host}:{httpd.server_address[1]}/"
    print(f"QEAAI OS 실행 중: {url}", flush=True)
    if realworld:
        loc = realworld.cfg.location
        print(f"실제 환경  : {loc.name} ({loc.lat:.4f}, {loc.lon:.4f})  날씨 {'Open-Meteo' if realworld.cfg.weather else '꺼짐'}"
              f"  실측 센서 {'이 컴퓨터' if realworld.sensors else '없음'}", flush=True)
        if realworld.broker:
            b = realworld.broker
            print(f"MQTT 브로커: {b.url}  (장치 -> qeaai/things/<id>/state, 트윈 -> qeaai/twin/#)"
                  f"{'  인증 사용' if b.username else ''}", flush=True)
        elif realworld.mqtt_error:
            print(f"MQTT 브로커: 시작 실패 - {realworld.mqtt_error}", flush=True)
        print(f"WoT 트윈   : {url}api/things", flush=True)
    print("VS Code: Ctrl+Shift+P -> 'Open Integrated Browser' (또는 'Simple Browser: Show') -> 위 주소 입력", flush=True)
    print("종료: Ctrl+C", flush=True)
    try:
        httpd.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        sim.stop()
        sim.agent.save()
        httpd.server_close()
        if realworld:
            realworld.stop()

"""QEAAI 시각화 서버 - OS / VR / Game 을 브라우저 화면으로.

표준 라이브러리만 사용한다.
    GET  /              대시보드 (static/index.html)
    GET  /api/info      셀 사양 + 양자 커패시턴스 곡선
    GET  /api/stream    Server-Sent Events, 틱마다 전체 상태
    POST /api/control   {"gesture": "fist"} | {"mode": "demo"|"manual"} |
                        {"wearer": "owner"|"impostor"} | {"recharge": true} | {"reset": true}
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..bio.emg import CHANNELS, GESTURES, EMGSimulator, UserPhysiology
from ..materials.quantum import capacitance_breakdown
from ..system.kernel import QEAAIKernel

STATIC_DIR = Path(__file__).parent / "static"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}

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


class Simulation:
    def __init__(self, seed: int = 0):
        self.seed = seed
        self.cond = threading.Condition()
        self.running = False
        self._thread: threading.Thread | None = None
        self._build()

    # --- 구성 -------------------------------------------------------------
    def _build(self) -> None:
        self.kernel = QEAAIKernel.build_default(seed=self.seed)
        self.owner_source = self.kernel.source
        self.impostor_source = EMGSimulator(UserPhysiology.random("impostor", self.seed + 999), seed=self.seed + 3)
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
        return gesture, f"AUTO {index + 1}/{len(WEB_DEMO_LOOP)} · {LABEL_KO.get(gesture, gesture)} · {remaining:3.1f}s"

    def step(self) -> dict:
        """한 틱 진행하고 브라우저로 보낼 상태를 만든다 (cond 를 잡은 상태에서 호출)."""
        k = self.kernel
        dt = k.dt
        gesture, script_label = self._current_gesture()
        prev_alt = k.game.state.altitude_m
        frame = k.tick(gesture)
        f = frame.flight

        h = math.radians(f.heading_deg)
        self.x += f.speed_mps * math.sin(h) * dt
        self.y += f.speed_mps * math.cos(h) * dt
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
            if path == "/api/state":
                _, payload = sim.wait_payload(-1, timeout=0)
                return self._send(HTTPStatus.OK, (payload or "{}").encode(), "application/json; charset=utf-8")
            name = "index.html" if path in ("/", "") else path.lstrip("/")
            file = (STATIC_DIR / name).resolve()
            if STATIC_DIR.resolve() not in file.parents or not file.is_file():
                return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            self._send(HTTPStatus.OK, file.read_bytes(), CONTENT_TYPES.get(file.suffix, "application/octet-stream"))

        def do_POST(self) -> None:
            if self.path != "/api/control":
                return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            try:
                length = int(self.headers.get("Content-Length", "0"))
                cmd = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(cmd, dict):
                    raise ValueError("body must be a JSON object")
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


def serve(host: str = "127.0.0.1", port: int = 8765, seed: int = 0, port_tries: int = 10) -> None:
    sim = Simulation(seed=seed)
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
    print("VS Code: Ctrl+Shift+P -> 'Open Integrated Browser' (또는 'Simple Browser: Show') -> 위 주소 입력", flush=True)
    print("종료: Ctrl+C", flush=True)
    try:
        httpd.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        sim.stop()
        httpd.server_close()

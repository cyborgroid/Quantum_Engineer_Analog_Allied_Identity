"""Allied Identity 에이전트 - "너한테 목소리로 말 걸지 않는다. 그것이 너이고, 네가 컨트롤 한다."

음성 명령을 기다리는 비서가 아니라, 사용자의 근육 신호와 실제 환경을 보고 스스로 장치를 제어하는 AI.
    * 페르소나: 사용자의 특성(동작 습관, 힘, 활동 시간대, 비행 성향)을 계속 학습하고 파일에 남긴다
                -> "AI 가 나 자신을 나타내게"
    * 근육 -> 장치: 주먹을 1 초 유지하면 실제 MQTT 액추에이터 켜기, 손을 펴서 1 초 유지하면 끄기
    * 자율 판단: 타인 착용 -> 모든 액추에이터 차단 / 배터리 부족 -> 절전 / 밤 -> 화면 어둡게 / 강풍 경고
명령은 MQTT 표준 명령 토픽 qeaai/things/<id>/set 으로 실제 장치에 간다.
MCP 서버(mcp_server.py)를 통해 Claude 도 같은 에이전트에 명령할 수 있다.
"""

from __future__ import annotations

import json
import time
from collections import Counter, deque
from pathlib import Path
from typing import Callable

Command = Callable[[str, dict], None]  # (device id, properties)
HOLD_S = 1.0
SAVER_ON_SOC, SAVER_OFF_SOC = 0.30, 0.50


class PersonaProfile:
    """'나의 전체적인 특성' - 세션을 넘어 누적된다."""

    def __init__(self) -> None:
        self.gesture_s: Counter[str] = Counter()
        self.effort_sum: Counter[str] = Counter()
        self.hours = [0.0] * 24
        self.airborne_s = 0.0
        self.alt_sum = 0.0
        self.bank_abs_sum = 0.0
        self.throttle_sum = 0.0
        self.total_s = 0.0

    def update(self, gesture: str | None, activation: float, flight: dict, hour: int, dt: float) -> None:
        self.total_s += dt
        self.hours[hour % 24] += dt
        if gesture:
            self.gesture_s[gesture] += dt
            self.effort_sum[gesture] += activation * dt
        if flight.get("airborne"):
            self.airborne_s += dt
            self.alt_sum += flight["altitude_m"] * dt
            self.bank_abs_sum += abs(flight["bank_deg"]) * dt
            self.throttle_sum += flight["throttle"] * dt

    def summary(self) -> dict:
        active = {g: s for g, s in self.gesture_s.items() if g != "rest"}
        fav = max(active, key=active.get) if active else None
        effort = {g: round(self.effort_sum[g] / s, 2) for g, s in self.gesture_s.items() if s > 0}
        peak_hour = max(range(24), key=lambda h: self.hours[h]) if self.total_s else None
        air = self.airborne_s
        bank = self.bank_abs_sum / air if air else 0.0
        thr = self.throttle_sum / air if air else 0.0
        style = None
        if air > 5:
            style = "공격적" if bank > 20 or thr > 0.7 else "안정적" if bank < 8 and thr < 0.5 else "균형형"
        return {
            "observed_min": round(self.total_s / 60, 1),
            "favorite_gesture": fav,
            "effort_by_gesture": effort,
            "peak_hour": peak_hour,
            "airborne_min": round(air / 60, 1),
            "mean_altitude_m": round(self.alt_sum / air, 1) if air else None,
            "mean_bank_deg": round(bank, 1),
            "flight_style": style,
        }

    def to_dict(self) -> dict:
        return {"gesture_s": dict(self.gesture_s), "effort_sum": dict(self.effort_sum), "hours": self.hours,
                "airborne_s": self.airborne_s, "alt_sum": self.alt_sum, "bank_abs_sum": self.bank_abs_sum,
                "throttle_sum": self.throttle_sum, "total_s": self.total_s}

    @classmethod
    def from_dict(cls, d: dict) -> "PersonaProfile":
        p = cls()
        p.gesture_s.update(d.get("gesture_s", {}))
        p.effort_sum.update(d.get("effort_sum", {}))
        hours = d.get("hours", [])
        if len(hours) == 24:
            p.hours = [float(h) for h in hours]
        for k in ("airborne_s", "alt_sum", "bank_abs_sum", "throttle_sum", "total_s"):
            setattr(p, k, float(d.get(k, 0.0)))
        return p


class AlliedAgent:
    def __init__(self, command: Command | None = None, persona_path: str | Path | None = None):
        self.command = command
        self.persona_path = Path(persona_path) if persona_path else None
        self.persona = self._load()
        self.log: deque[dict] = deque(maxlen=40)
        self.locked = False
        self.saver = False
        self.night_dim: bool | None = None
        self.windy = False
        self._hold_g: str | None = None
        self._hold_s = 0.0
        self._fired = False
        self._save_timer = 0.0
        self._t = 0.0

    # --- 영속 -----------------------------------------------------------------
    def _load(self) -> PersonaProfile:
        try:
            if self.persona_path and self.persona_path.is_file():
                return PersonaProfile.from_dict(json.loads(self.persona_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        return PersonaProfile()

    def save(self) -> None:
        if not self.persona_path:
            return
        try:
            self.persona_path.parent.mkdir(parents=True, exist_ok=True)
            self.persona_path.write_text(json.dumps(self.persona.to_dict()), encoding="utf-8")
        except OSError:
            pass

    # --- 행동 -----------------------------------------------------------------
    def _note(self, text: str, kind: str = "info") -> None:
        self.log.appendleft({"t": round(self._t, 1), "time": time.strftime("%H:%M:%S"), "text": text, "kind": kind})

    def send(self, device: str, props: dict, reason: str, kind: str = "control") -> None:
        if self.command:
            self.command(device, props)
        self._note(f"{device} ← {json.dumps(props, ensure_ascii=False)} ({reason})", kind)

    @staticmethod
    def actuators(twin: list[dict]) -> list[dict]:
        return sorted((d for d in twin if d.get("source") == "mqtt" and ("on" in d or "actuator" in str(d.get("kind", "")))),
                      key=lambda d: d["id"])

    def step(self, state: dict, kernel, dt: float) -> None:
        """시뮬레이션 틱마다 호출 (kernel 은 전력 부하를 바꾸기 위해)."""
        self._t = state["t"]
        d = state.get("decision")
        env = state.get("env") or {}
        hour = int((env.get("local_time") or time.strftime("%Y-%m-%d %H:%M"))[11:13])
        trusted = bool(d and d["trusted"])
        self.persona.update(d["intent"] if trusted else None, state["activation"], state["flight"], hour, dt)
        acts = self.actuators(state["twin"])

        # 1) 안전: 타인 착용 -> 모든 액추에이터 차단
        if d and not trusted and not self.locked:
            self.locked = True
            self._note("페어링되지 않은 사람 감지: 제어 잠금", "alert")
            for a in acts:
                if a.get("on"):
                    self.send(a["id"], {"on": False}, "타인 착용 차단", "alert")
        elif trusted and self.locked:
            self.locked = False
            self._note("본인 재확인: 제어 잠금 해제", "info")

        # 2) 근육 -> 장치: 동작을 1 초 유지하면 (목소리 없이)
        g = d["intent"] if trusted else None
        if g != self._hold_g:
            self._hold_g, self._hold_s, self._fired = g, 0.0, False
        else:
            self._hold_s += dt
        if trusted and not self._fired and self._hold_s >= HOLD_S and g in ("fist", "open") and acts:
            self._fired = True
            target = acts[0]
            want = g == "fist"
            if bool(target.get("on")) != want:
                self.send(target["id"], {"on": want}, f"{'주먹' if want else '손 펴기'} {HOLD_S:.0f}초 유지")

        # 3) 전력 자율: 배터리 부족 -> 절전 (무선 송신 주기, 화면 전력 축소)
        soc = state["power"]["soc"]
        loads = kernel.power.loads
        if not self.saver and soc < SAVER_ON_SOC:
            self.saver = True
            loads["radio"].duty, loads["display"].power_w = 0.05, 0.3e-3
            self._note(f"SOC {soc * 100:.0f}%: 절전 모드 (무선 5%, 화면 0.3 mW)", "power")
        elif self.saver and soc > SAVER_OFF_SOC:
            self.saver = False
            loads["radio"].duty = 0.2
            self._note(f"SOC {soc * 100:.0f}%: 절전 해제", "power")

        # 4) 실제 환경: 밤이면 화면을 어둡게, 강풍 경고
        sun = (env.get("sun") or {}).get("elevation_deg")
        if sun is not None:
            night = sun < -6
            if night != self.night_dim:
                self.night_dim = night
                self._note("밤 (실제 태양 고도): 화면 밝기 낮춤" if night else "낮: 화면 밝기 복원", "env")
            if not self.saver:
                loads["display"].power_w = 0.5e-3 if night else 0.8e-3
        w = env.get("weather") or {}
        windy = (w.get("wind_gusts_10m") or 0) > 10
        if windy != self.windy:
            self.windy = windy
            self._note(f"강풍 주의: 돌풍 {w.get('wind_gusts_10m')} m/s (실측)" if windy else "바람 잦아듦", "env")

        self._save_timer += dt
        if self._save_timer > 30:
            self._save_timer = 0.0
            self.save()

    def export(self) -> dict:
        return {
            "persona": self.persona.summary(),
            "locked": self.locked,
            "saver": self.saver,
            "night_dim": bool(self.night_dim),
            "log": list(self.log)[:8],
        }

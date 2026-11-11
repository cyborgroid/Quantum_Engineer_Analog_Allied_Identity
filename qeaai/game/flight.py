"""Game - "디바이스와 게임기 조종하듯 컨트롤 하면 이미 날 수 있다".

근육 의도가 곧 조종간이다:
    fist        -> 스로틀 올림
    open        -> 스로틀 내림
    wrist_left  -> 왼쪽 뱅크
    wrist_right -> 오른쪽 뱅크
    rest        -> 수평 복귀, 스로틀 유지
    None        -> 제어권 없음(fail-safe): 수평 복귀 + 스로틀 서서히 감소
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

GRAVITY = 9.81
TAKEOFF_SPEED = 10.0  # m/s
MAX_BANK = 45.0  # deg


@dataclass
class FlightState:
    throttle: float = 0.0
    speed_mps: float = 0.0
    altitude_m: float = 0.0
    heading_deg: float = 0.0
    bank_deg: float = 0.0

    @property
    def airborne(self) -> bool:
        return self.altitude_m > 0.0

    def as_dict(self) -> dict:
        return {**asdict(self), "airborne": self.airborne}


class FlightGame:
    def __init__(self) -> None:
        self.state = FlightState()

    def apply(self, intent: str | None, confidence: float, dt: float) -> FlightState:
        s = self.state
        k = max(0.0, min(confidence, 1.0))

        if intent == "fist":
            s.throttle += 0.5 * dt * k
        elif intent == "open":
            s.throttle -= 0.5 * dt * k
        elif intent == "wrist_left":
            s.bank_deg -= 30.0 * dt * k
        elif intent == "wrist_right":
            s.bank_deg += 30.0 * dt * k
        else:  # rest 또는 제어권 없음 -> 수평 복귀
            s.bank_deg -= s.bank_deg * min(1.0, 1.5 * dt)
            if intent is None:
                s.throttle -= 0.3 * dt
        s.throttle = min(max(s.throttle, 0.0), 1.0)
        s.bank_deg = min(max(s.bank_deg, -MAX_BANK), MAX_BANK)

        # 종단속도: 스로틀 1 에서 20 m/s
        s.speed_mps = max(0.0, s.speed_mps + (8.0 * s.throttle - 0.02 * s.speed_mps**2) * dt)

        if s.speed_mps > TAKEOFF_SPEED:
            s.altitude_m += 0.8 * (s.speed_mps - TAKEOFF_SPEED) * dt
        elif s.altitude_m > 0:
            s.altitude_m -= 2.0 * dt
        s.altitude_m = max(s.altitude_m, 0.0)

        if s.airborne and s.speed_mps > 1.0:
            turn_rate = math.degrees(GRAVITY * math.tan(math.radians(s.bank_deg)) / s.speed_mps)
            s.heading_deg = (s.heading_deg + turn_rate * dt) % 360.0
        return s

"""근육 신호(sEMG) - "AI 가 사용자 신체 근육의 생물학적 구동으로 작동".

손목밴드 위치의 전완근 4채널 표면 근전도를 시뮬레이션한다.
사람마다 근육 게인과 동작 습관(bias)이 달라서, 같은 동작이라도
신호 패턴이 다르다 -> Allied Identity 가 '그 사람'과 페어링되어야 하는 이유.

실제 하드웨어로 넘어갈 때는 EMGSimulator 대신 같은 인터페이스
(set_gesture / read) 를 가진 센서 드라이버로 교체하면 된다.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

CHANNELS: tuple[str, ...] = ("flexor", "extensor", "radial", "ulnar")

# 동작별 채널 활성도 (0..1, 대략 mV 단위 진폭)
GESTURES: dict[str, tuple[float, ...]] = {
    "rest": (0.02, 0.02, 0.02, 0.02),
    "fist": (0.80, 0.35, 0.30, 0.30),
    "open": (0.25, 0.80, 0.25, 0.25),
    "wrist_left": (0.30, 0.30, 0.75, 0.10),
    "wrist_right": (0.30, 0.30, 0.10, 0.75),
}

Window = list[list[float]]  # [channel][sample]


@dataclass(frozen=True)
class UserPhysiology:
    """한 사람의 근육 특성."""

    user_id: str
    channel_gain: tuple[float, ...]
    gesture_bias: dict[str, tuple[float, ...]] = field(default_factory=dict)
    noise_floor_mv: float = 0.01

    @classmethod
    def random(cls, user_id: str, seed: int) -> "UserPhysiology":
        rng = random.Random(seed)
        gains = tuple(rng.uniform(0.6, 1.4) for _ in CHANNELS)
        bias = {
            g: tuple(0.0 if g == "rest" else rng.uniform(-0.15, 0.15) for _ in CHANNELS)
            for g in GESTURES
        }
        return cls(user_id=user_id, channel_gain=gains, gesture_bias=bias)

    def amplitudes(self, gesture: str, effort: float = 1.0) -> list[float]:
        pattern = GESTURES[gesture]
        bias = self.gesture_bias.get(gesture, (0.0,) * len(CHANNELS))
        return [max(0.0, g * (p + b) * effort) for g, p, b in zip(self.channel_gain, pattern, bias)]


class EMGSimulator:
    """사용자 한 명의 손목밴드 EMG 스트림."""

    def __init__(self, physiology: UserPhysiology, sample_rate_hz: int = 1000, seed: int = 0):
        self.physiology = physiology
        self.sample_rate_hz = sample_rate_hz
        self._rng = random.Random(seed)
        self.gesture = "rest"
        self.effort = 1.0

    def set_gesture(self, gesture: str, effort: float = 1.0) -> None:
        if gesture not in GESTURES:
            raise ValueError(f"unknown gesture {gesture!r}; known: {sorted(GESTURES)}")
        self.gesture = gesture
        self.effort = effort

    def read(self, duration_s: float = 0.2) -> Window:
        n = max(2, int(duration_s * self.sample_rate_hz))
        amps = self.physiology.amplitudes(self.gesture, self.effort)
        noise = self.physiology.noise_floor_mv
        window: Window = []
        for a in amps:
            a *= self._rng.gauss(1.0, 0.10)  # 시행 간 힘 변동
            a = max(a, 0.0)
            window.append([a * self._rng.gauss(0, 1) + noise * self._rng.gauss(0, 1) for _ in range(n)])
        return window


def _rms(xs: list[float]) -> float:
    return math.sqrt(sum(x * x for x in xs) / len(xs))


def extract_features(window: Window) -> list[float]:
    """채널별 [log RMS, RMS 비율, 파형 길이/샘플] -> 12 차원 특징 벡터."""
    rms = [_rms(ch) for ch in window]
    total = sum(rms) or 1e-12
    wl = [sum(abs(b - a) for a, b in zip(ch, ch[1:])) / (len(ch) - 1) for ch in window]
    return (
        [math.log(r + 1e-6) for r in rms]
        + [r / total for r in rms]
        + [math.log(w + 1e-6) for w in wl]
    )


def activation_level(window: Window, reference_mv: float = 0.8) -> float:
    """전체 근육 활성도 0..1 (하베스터 / 대시보드용)."""
    mean_rms = sum(_rms(ch) for ch in window) / len(window)
    return min(1.0, mean_rms / reference_mv)

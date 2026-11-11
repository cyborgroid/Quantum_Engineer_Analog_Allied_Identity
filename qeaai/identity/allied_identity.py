"""Allied Identity - "자체 완성된 AI 가 아니라 사람과 호환해야 비로소 완성된 AI".

설계 원칙 (명세서 [목표] / [환경]):
  * 페어링 전에는 아무 것도 하지 못한다 (NotPairedError).
  * 목소리 명령이 아니라 사용자의 근육 신호 자체가 입력이다.
  * 페어링된 그 사람의 신호 패턴을 '자신'으로 삼고, 다른 사람이 착용하면
    신뢰도(trust)가 떨어져 제어를 넘겨주지 않는다.
  * 사용하면서 그 사람에게 계속 적응한다 (온라인 centroid 업데이트).

모델: 클래스 내 분산으로 정규화한 공간에서의 최근접 centroid 분류기.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from ..bio.emg import GESTURES, EMGSimulator, extract_features

Vector = list[float]


class NotPairedError(RuntimeError):
    pass


@dataclass(frozen=True)
class Decision:
    intent: str
    confidence: float
    distance: float
    authenticated: bool  # 이번 창이 페어링된 사용자 패턴 안에 있는가
    trust: float  # 누적 신뢰도 0..1
    trusted: bool  # 제어권을 넘겨줄 수 있는가
    scores: dict[str, float] = field(default_factory=dict)  # 동작별 확률


def collect_calibration(sim: EMGSimulator, windows_per_gesture: int = 20, duration_s: float = 0.2) -> dict[str, list[Vector]]:
    """사용자가 각 동작을 몇 번씩 해서 페어링 데이터를 만든다."""
    samples: dict[str, list[Vector]] = {}
    for gesture in GESTURES:
        sim.set_gesture(gesture)
        samples[gesture] = [extract_features(sim.read(duration_s)) for _ in range(windows_per_gesture)]
    sim.set_gesture("rest")
    return samples


class AlliedIdentity:
    TRUST_DECAY = 0.85
    TRUST_THRESHOLD = 0.5

    def __init__(self, name: str = "QEAAI", adapt_rate: float = 0.05):
        self.name = name
        self.adapt_rate = adapt_rate
        self.paired_user: str | None = None
        self._scale: Vector = []
        self._centroids: dict[str, Vector] = {}
        self.auth_threshold: float = math.inf
        self.trust: float = 0.0

    # --- 페어링 -----------------------------------------------------------
    @property
    def is_paired(self) -> bool:
        return self.paired_user is not None

    def pair(self, user_id: str, samples: dict[str, list[Vector]]) -> None:
        if len(samples) < 2 or any(len(v) < 3 for v in samples.values()):
            raise ValueError("need >=2 gestures with >=3 windows each")
        dim = len(next(iter(samples.values()))[0])

        raw_centroids = {g: [sum(r[i] for r in rows) / len(rows) for i in range(dim)] for g, rows in samples.items()}
        sq = [0.0] * dim
        n = 0
        for g, rows in samples.items():
            c = raw_centroids[g]
            for r in rows:
                for i in range(dim):
                    sq[i] += (r[i] - c[i]) ** 2
                n += 1
        dof = max(n - len(samples), 1)
        self._scale = [max(math.sqrt(s / dof), 1e-3) for s in sq]
        self._centroids = {g: self._z(c) for g, c in raw_centroids.items()}

        own = sorted(min(self._distances(self._z(r)).values()) for rows in samples.values() for r in rows)
        p99 = own[int(0.99 * (len(own) - 1))]
        self.auth_threshold = p99 * 1.25
        self.paired_user = user_id
        self.trust = 1.0

    def on_worn(self) -> None:
        """밴드를 새로 착용함: 누가 찼는지 모르므로 신뢰도를 경계값에서 다시 시작."""
        if self.is_paired:
            self.trust = self.TRUST_THRESHOLD

    def unpair(self) -> None:
        self.__init__(self.name, self.adapt_rate)

    # --- 추론 -------------------------------------------------------------
    def _z(self, v: Vector) -> Vector:
        return [x / s for x, s in zip(v, self._scale)]

    def _distances(self, z: Vector) -> dict[str, float]:
        return {g: math.dist(z, c) for g, c in self._centroids.items()}

    def decide(self, features: Vector) -> Decision:
        if not self.is_paired:
            raise NotPairedError(f"{self.name}: Allied Identity 는 사람과 페어링되어야 완성됩니다 (pair() 먼저)")
        z = self._z(features)
        dists = self._distances(z)
        intent = min(dists, key=dists.get)
        best = dists[intent]

        logits = {g: -0.5 * d * d for g, d in dists.items()}
        top = max(logits.values())
        exp = {g: math.exp(l - top) for g, l in logits.items()}
        z_sum = sum(exp.values())
        scores = {g: e / z_sum for g, e in exp.items()}
        confidence = scores[intent]

        authenticated = best <= self.auth_threshold
        # 휴식 상태는 누구나 비슷하므로 신원 판단 근거로 쓰지 않는다
        if intent != "rest":
            self.trust = self.TRUST_DECAY * self.trust + (1 - self.TRUST_DECAY) * (1.0 if authenticated else 0.0)
        trusted = self.trust >= self.TRUST_THRESHOLD

        if authenticated and trusted and confidence > 0.9 and self.adapt_rate > 0:
            c = self._centroids[intent]
            self._centroids[intent] = [ci + self.adapt_rate * (zi - ci) for ci, zi in zip(c, z)]

        return Decision(intent, confidence, best, authenticated, self.trust, trusted, scores)

    def evaluate(self, labelled: Iterable[tuple[str, Vector]]) -> float:
        """정확도 (적응 없이)."""
        rate, self.adapt_rate = self.adapt_rate, 0.0
        trust = self.trust
        try:
            results = [self.decide(f).intent == g for g, f in labelled]
        finally:
            self.adapt_rate, self.trust = rate, trust
        return sum(results) / len(results) if results else 0.0

    # --- 저장 -------------------------------------------------------------
    def to_dict(self) -> dict:
        if not self.is_paired:
            raise NotPairedError("nothing to save before pairing")
        return {
            "name": self.name,
            "paired_user": self.paired_user,
            "adapt_rate": self.adapt_rate,
            "scale": self._scale,
            "centroids": self._centroids,
            "auth_threshold": self.auth_threshold,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AlliedIdentity":
        ai = cls(data["name"], data["adapt_rate"])
        ai.paired_user = data["paired_user"]
        ai._scale = list(data["scale"])
        ai._centroids = {g: list(c) for g, c in data["centroids"].items()}
        ai.auth_threshold = float(data["auth_threshold"])
        ai.trust = 1.0
        return ai

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "AlliedIdentity":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

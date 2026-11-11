"""근육 구동 에너지 하베스터 - "물리적 엔진 대신 사용자 근육의 생물학적 구동".

손목 움직임을 압전/마찰전기(TENG) 소자로 전기로 바꾸는 웨어러블 하베스터 모델.
손목 위치 웨어러블의 현실적인 출력은 수십~수백 uW 수준이므로 기본값은 150 uW.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MuscleHarvester:
    peak_power_w: float = 150e-6
    exponent: float = 2.0  # 움직임 세기의 제곱에 비례 (운동에너지 ~ v^2)

    def power(self, activation: float) -> float:
        a = min(max(activation, 0.0), 1.0)
        return self.peak_power_w * a**self.exponent

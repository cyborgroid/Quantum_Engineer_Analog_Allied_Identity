"""QE: SNSS_Battery 소재 안의 실제 양자 효과 - 그래핀 양자 커패시턴스.

그래핀은 디랙점 근처의 상태밀도가 매우 작아서, 전극에 전하를 넣으려면
전자의 양자역학적 상태가 채워져야 한다. 이 '양자 커패시턴스(C_q)'가
전기이중층 커패시턴스(C_dl)와 직렬로 연결되어 전체 용량을 제한한다.

    C_q(V) = (2 e^2 k_B T) / (pi (hbar v_F)^2) * ln[ 2 (1 + cosh(e V / k_B T)) ]

명세서의 "SNSS Battery 소재가 양자화로 나아가게 하는 첫걸음" 을
측정 가능한 물리량으로 옮긴 부분이다.
"""

from __future__ import annotations

import math

ELEMENTARY_CHARGE = 1.602176634e-19  # C
BOLTZMANN = 1.380649e-23  # J/K
HBAR = 1.054571817e-34  # J*s
GRAPHENE_FERMI_VELOCITY = 1.0e6  # m/s


def graphene_quantum_capacitance(
    potential_v: float,
    temperature_k: float = 298.15,
    fermi_velocity: float = GRAPHENE_FERMI_VELOCITY,
) -> float:
    """단층 그래핀의 면적당 양자 커패시턴스 [F/m^2].

    potential_v 는 디랙점 기준 채널 전위. V=0 에서 최소(~0.8 uF/cm^2)이고
    |V| 가 커질수록 선형으로 증가한다.
    """
    if temperature_k <= 0:
        raise ValueError("temperature_k must be positive")
    kt = BOLTZMANN * temperature_k
    x = abs(ELEMENTARY_CHARGE * potential_v / kt)
    # ln(2(1+cosh x)) = |x| + 2 ln(1 + e^-|x|)  (큰 |x| 에서도 오버플로 없음)
    ln_term = x + 2.0 * math.log1p(math.exp(-x))
    prefactor = 2.0 * ELEMENTARY_CHARGE**2 * kt / (math.pi * (HBAR * fermi_velocity) ** 2)
    return prefactor * ln_term


def capacitance_breakdown(
    potential_v: float,
    edl_capacitance_f_per_m2: float,
    temperature_k: float = 298.15,
) -> dict[str, float]:
    """전극 한 쪽의 면적당 용량 분해.

    quantum_limit 은 C_total / C_dl: 1 에 가까우면 고전적(EDL) 거동,
    작을수록 양자 효과가 용량을 지배한다.
    """
    cq = graphene_quantum_capacitance(potential_v, temperature_k)
    cdl = edl_capacitance_f_per_m2
    total = cq * cdl / (cq + cdl)
    return {"c_quantum": cq, "c_edl": cdl, "c_total": total, "quantum_limit": total / cdl}

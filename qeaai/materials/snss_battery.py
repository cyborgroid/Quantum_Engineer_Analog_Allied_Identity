"""SNSS_Battery: 그래핀 폴리머 + 멸균 생리 식염수(0.9% NaCl) + PVA 셀.

화학적으로 이 조합(그래핀 전극 / 중성 수계 전해질 / PVA 하이드로겔)은
산화환원 배터리가 아니라 전기이중층 커패시터(EDLC, 슈퍼커패시터)로 동작한다.
그래서 이 모델은 EDLC 로 계산한다:

    - 용량: 전극 면적 x (C_q 와 C_dl 의 직렬)       -> quantum.py
    - 전압창: 중성 수계 전해질은 물 분해 때문에 ~0.8 V
    - ESR: 전해질 이온전도도와 분리막 두께로 계산
    - 자가방전: 누설 저항

실제 셀을 만들고 CV/GCD 측정을 하면 Electrode / Electrolyte / CellGeometry
파라미터를 측정값으로 교체해서 이 모델을 '보정'하면 된다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .quantum import graphene_quantum_capacitance


@dataclass(frozen=True)
class Electrolyte:
    """식염수 + PVA 하이드로겔 전해질."""

    nacl_wt_percent: float = 0.9  # 생리 식염수
    pva_wt_percent: float = 10.0
    temperature_c: float = 25.0

    @property
    def temperature_k(self) -> float:
        return self.temperature_c + 273.15

    @property
    def conductivity(self) -> float:
        """이온 전도도 [S/m].

        0.9% NaCl 은 25 C 에서 약 1.6 S/m. 저농도 영역에서는 농도에 거의 비례하고,
        온도계수는 약 2 %/C. PVA 겔 네트워크가 이온 이동을 늦추는 효과는
        지수 감쇠로 근사한다 (10 wt% PVA -> 약 0.5 배).
        """
        base = 1.6 * (self.nacl_wt_percent / 0.9)
        temp = 1.0 + 0.02 * (self.temperature_c - 25.0)
        gel = math.exp(-self.pva_wt_percent / 15.0)
        return max(base * temp * gel, 1e-6)


@dataclass(frozen=True)
class Electrode:
    """그래핀-폴리머 복합 전극 (한 쪽)."""

    mass_mg: float = 50.0
    specific_area_m2_per_g: float = 500.0  # 유효 비표면적 (이론 최대 2630)
    edl_capacitance_f_per_m2: float = 0.10  # 10 uF/cm^2, 수계 헬름홀츠층

    @property
    def area_m2(self) -> float:
        return self.mass_mg * 1e-3 * self.specific_area_m2_per_g

    def capacitance(self, potential_v: float, temperature_k: float) -> float:
        """전극 한 쪽의 미분 용량 [F]."""
        cq = graphene_quantum_capacitance(potential_v, temperature_k)
        cdl = self.edl_capacitance_f_per_m2
        return self.area_m2 * (cq * cdl / (cq + cdl))


@dataclass(frozen=True)
class CellGeometry:
    footprint_cm2: float = 10.0  # 손목밴드 한 칸 크기
    separator_mm: float = 0.5
    contact_resistance_ohm: float = 0.3


@dataclass
class SNSSCell:
    electrode: Electrode = field(default_factory=Electrode)
    electrolyte: Electrolyte = field(default_factory=Electrolyte)
    geometry: CellGeometry = field(default_factory=CellGeometry)
    max_voltage: float = 0.8
    leakage_ohm: float = 100_000.0
    voltage: float = 0.0

    # --- 정적 특성 -------------------------------------------------------
    def capacitance(self, voltage: float | None = None) -> float:
        """셀 미분 용량 [F]. 대칭 셀: 전극 두 개 직렬, 각 전극에 V/2."""
        v = self.voltage if voltage is None else voltage
        return self.electrode.capacitance(v / 2.0, self.electrolyte.temperature_k) / 2.0

    @property
    def esr(self) -> float:
        area_m2 = self.geometry.footprint_cm2 * 1e-4
        thickness_m = self.geometry.separator_mm * 1e-3
        return thickness_m / (self.electrolyte.conductivity * area_m2) + self.geometry.contact_resistance_ohm

    def stored_energy(self, voltage: float | None = None, steps: int = 100) -> float:
        """저장 에너지 [J] = integral_0^V v * C(v) dv  (C 가 전압 의존이므로 적분)."""
        v_end = self.voltage if voltage is None else voltage
        if v_end <= 0:
            return 0.0
        h = v_end / steps
        total = 0.0
        for i in range(steps + 1):
            v = i * h
            w = 0.5 if i in (0, steps) else 1.0
            total += w * v * self.capacitance(v)
        return total * h

    @property
    def state_of_charge(self) -> float:
        full = self.stored_energy(self.max_voltage)
        return self.stored_energy() / full if full > 0 else 0.0

    def terminal_voltage(self, current_a: float) -> float:
        """방전 전류(+) 가 흐를 때 단자 전압."""
        return self.voltage - current_a * self.esr

    # --- 동적 거동 -------------------------------------------------------
    def step(self, current_a: float, dt: float) -> float:
        """current_a > 0 방전, < 0 충전. 누설 포함. 새 전압을 반환."""
        leak = self.voltage / self.leakage_ohm
        c = max(self.capacitance(), 1e-9)
        self.voltage -= (current_a + leak) * dt / c
        self.voltage = min(max(self.voltage, 0.0), self.max_voltage)
        return self.voltage


@dataclass
class SNSSPack:
    """직렬/병렬 셀 팩. 셀이 균형 상태라고 가정하고 대표 셀 하나로 계산한다."""

    cell: SNSSCell = field(default_factory=SNSSCell)
    cells_in_series: int = 5
    strings_in_parallel: int = 1
    interconnect_ohm: float = 0.0  # 셀 사이 SNSS 배선 저항 (밴드 설계에서)

    def __post_init__(self) -> None:
        if self.cells_in_series < 1 or self.strings_in_parallel < 1:
            raise ValueError("pack needs at least one cell")

    @classmethod
    def charged(cls, **kwargs) -> "SNSSPack":
        pack = cls(**kwargs)
        pack.cell.voltage = pack.cell.max_voltage
        return pack

    @property
    def cell_count(self) -> int:
        return self.cells_in_series * self.strings_in_parallel

    @property
    def voltage(self) -> float:
        return self.cell.voltage * self.cells_in_series

    @property
    def max_voltage(self) -> float:
        return self.cell.max_voltage * self.cells_in_series

    @property
    def esr(self) -> float:
        return self.cell.esr * self.cells_in_series / self.strings_in_parallel + self.interconnect_ohm

    @property
    def capacitance(self) -> float:
        return self.cell.capacitance() * self.strings_in_parallel / self.cells_in_series

    @property
    def stored_energy(self) -> float:
        return self.cell.stored_energy() * self.cell_count

    @property
    def full_energy(self) -> float:
        return self.cell.stored_energy(self.cell.max_voltage) * self.cell_count

    @property
    def state_of_charge(self) -> float:
        return self.cell.state_of_charge

    def step(self, current_a: float, dt: float) -> float:
        self.cell.step(current_a / self.strings_in_parallel, dt)
        return self.voltage

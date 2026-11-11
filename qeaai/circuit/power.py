"""Circuit - "SNSS_Battery 를 제품으로 본다면 모든 디바이스 호환 가능".

팩 전압은 충전 상태에 따라 4.0 V -> 1 V 로 내려가므로(커패시터 특성),
DC-DC 컨버터로 디바이스 표준 전압(3.3 V)을 만든다.
근육 하베스터 전력은 같은 버스로 팩을 충전한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..materials.snss_battery import SNSSPack


@dataclass(frozen=True)
class DCDCConverter:
    output_v: float = 3.3
    min_input_v: float = 1.0
    peak_efficiency: float = 0.90

    def efficiency(self, input_v: float) -> float:
        if input_v <= 0:
            return 0.0
        return self.peak_efficiency * (1.0 - math.exp(-input_v / 0.8))


@dataclass
class Load:
    name: str
    power_w: float
    duty: float = 1.0
    enabled: bool = True

    @property
    def average_power_w(self) -> float:
        return self.power_w * self.duty if self.enabled else 0.0


@dataclass(frozen=True)
class PowerReport:
    pack_voltage: float
    state_of_charge: float
    load_w: float
    harvest_w: float
    pack_current_a: float
    brownout: bool


@dataclass
class PowerBus:
    pack: SNSSPack
    converter: DCDCConverter = field(default_factory=DCDCConverter)
    loads: dict[str, Load] = field(default_factory=dict)
    harvest_efficiency: float = 0.7

    def add_load(self, load: Load) -> None:
        self.loads[load.name] = load

    @property
    def load_w(self) -> float:
        return sum(l.average_power_w for l in self.loads.values())

    def step(self, dt: float, harvest_w: float = 0.0) -> PowerReport:
        load_w = self.load_w
        v_open = self.pack.voltage
        brownout = v_open < self.converter.min_input_v

        i_load = 0.0
        if not brownout and load_w > 0:
            i_load = load_w / (self.converter.efficiency(v_open) * v_open)
            for _ in range(3):  # ESR 전압강하 반영 고정점 반복
                v_term = v_open - i_load * self.pack.esr
                if v_term < self.converter.min_input_v:
                    brownout = True
                    i_load = 0.0
                    break
                i_load = load_w / (self.converter.efficiency(v_term) * v_term)

        i_harvest = harvest_w * self.harvest_efficiency / max(v_open, 0.5)
        net = i_load - i_harvest
        self.pack.step(net, dt)
        return PowerReport(
            pack_voltage=self.pack.voltage,
            state_of_charge=self.pack.state_of_charge,
            load_w=0.0 if brownout else load_w,
            harvest_w=harvest_w,
            pack_current_a=net,
            brownout=brownout,
        )

    def runtime_estimate_s(self, load_w: float | None = None) -> float:
        """현재 에너지로 버틸 수 있는 시간(대략, 평균 효율 0.8 가정)."""
        w = self.load_w if load_w is None else load_w
        return math.inf if w <= 0 else self.pack.stored_energy * 0.8 / w

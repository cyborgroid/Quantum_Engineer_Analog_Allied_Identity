import math

import pytest

from qeaai.circuit import Load, PowerBus
from qeaai.materials import SNSSCell, SNSSPack, capacitance_breakdown, graphene_quantum_capacitance


def test_quantum_capacitance_dirac_point_matches_literature():
    # 상온 디랙점 그래핀 C_q ~ 0.8 uF/cm^2
    cq = graphene_quantum_capacitance(0.0, 300.0) * 100  # uF/cm^2
    assert 0.7 < cq < 1.0


def test_quantum_capacitance_symmetric_and_increasing():
    assert graphene_quantum_capacitance(0.2) == pytest.approx(graphene_quantum_capacitance(-0.2))
    values = [graphene_quantum_capacitance(v) for v in (0, 0.05, 0.1, 0.2, 0.4)]
    assert values == sorted(values)
    assert math.isfinite(graphene_quantum_capacitance(50.0))  # 오버플로 없음


def test_quantum_limit_strongest_at_dirac_point():
    near = capacitance_breakdown(0.0, 0.10)["quantum_limit"]
    far = capacitance_breakdown(0.4, 0.10)["quantum_limit"]
    assert near < 0.2 < far < 1.0


def test_cell_energy_and_voltage_window():
    cell = SNSSCell()
    assert cell.stored_energy(0.0) == 0.0
    assert cell.stored_energy(0.8) > cell.stored_energy(0.4) > 0
    cell.voltage = 0.8
    cell.step(-1.0, 10.0)  # 과충전 시도
    assert cell.voltage == pytest.approx(0.8)
    v0 = cell.voltage
    cell.step(1e-3, 1.0)
    assert cell.voltage < v0


def test_saline_conductivity_drops_in_gel():
    from qeaai.materials import Electrolyte

    assert Electrolyte(pva_wt_percent=0).conductivity == pytest.approx(1.6)
    assert Electrolyte(pva_wt_percent=10).conductivity < 1.0


def test_pack_discharge_and_brownout():
    bus = PowerBus(SNSSPack.charged())
    bus.add_load(Load("heavy", 0.5))
    reports = [bus.step(1.0) for _ in range(120)]
    assert reports[0].state_of_charge > reports[-1].state_of_charge
    assert reports[-1].brownout


def test_harvest_charges_pack():
    pack = SNSSPack(cells_in_series=2)
    pack.cell.voltage = 0.3
    bus = PowerBus(pack)
    v0 = pack.voltage
    bus.step(10.0, harvest_w=1e-3)
    assert pack.voltage > v0

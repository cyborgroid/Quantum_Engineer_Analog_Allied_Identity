"""명세 재검토에서 찾은 항목: 액정 소재 / 전자 타투 / 3D 펜 설계 / Allied Identity 에이전트 / MCP / VPN."""

import io
import json
import shutil
import threading
from http.server import ThreadingHTTPServer

import pytest

from qeaai.agent.allied_agent import AlliedAgent, PersonaProfile
from qeaai.design.band import BandDesign
from qeaai.materials.snss_material import (GEL_INK, TRACE_INK, ETattooElectrode, SNSSInk, Trace,
                                           default_emg_noise_floor_mv, material_report)
from qeaai.system.kernel import QEAAIKernel
from qeaai.web.server import Simulation, make_handler


# --- 그래핀 산화물 액정 -------------------------------------------------------
def test_go_liquid_crystal_phase_transition():
    onset = SNSSInk().nematic_onset_mg_per_ml
    assert 0.3 < onset < 3.0  # 문헌: 큰 GO 조각은 ~1 mg/mL 근처에서 네마틱
    assert SNSSInk(go_mg_per_ml=onset * 0.5).phase == "isotropic"
    assert SNSSInk(go_mg_per_ml=onset * 3).phase == "nematic"
    big_flakes = SNSSInk(go_mg_per_ml=1.0, flake_um=30)
    assert big_flakes.nematic_onset_mg_per_ml < onset  # 조각이 클수록 낮은 농도에서 액정


def test_extrusion_aligns_lc_and_makes_conduction_anisotropic():
    ink = TRACE_INK
    shear = ink.extrusion_shear(0.7, 10)
    assert ink.order_parameter(shear) > ink.order_parameter(0)
    along, across = ink.conductivity(ink.order_parameter(shear))
    assert 2 < along / across < 10
    iso = SNSSInk(go_mg_per_ml=0.2)
    assert iso.order_parameter(shear) < 0.1  # 등방성 분산액은 거의 정렬 안 됨


def test_trace_ink_vs_gel_ink():
    t = Trace(40, 2, 100)
    s = TRACE_INK.order_parameter(100)
    assert t.resistance(TRACE_INK, s) < 20
    assert t.resistance(GEL_INK, GEL_INK.order_parameter(100)) > 50 * t.resistance(TRACE_INK, s)


def test_etattoo_beats_thick_electrode():
    thin, thick = ETattooElectrode(thickness_um=0.5), ETattooElectrode(thickness_um=50, hydrogel_interface=False)
    assert 5 < thin.impedance_kohm() < 200
    assert thin.noise_uv_rms() < thick.noise_uv_rms() / 3
    assert material_report()["ink"]["phase"] == "nematic"


def test_kernel_uses_band_design_and_tattoo_noise():
    k = QEAAIKernel.build_default(seed=0)
    band = BandDesign()
    assert k.source.physiology.noise_floor_mv == pytest.approx(default_emg_noise_floor_mv())
    assert k.power.pack.interconnect_ohm == pytest.approx(band.trace_resistance_ohm())
    assert k.power.pack.cell.geometry.footprint_cm2 == pytest.approx(band.cell_footprint_cm2)


# --- 3D 펜 밴드 ---------------------------------------------------------------
@pytest.mark.parametrize("wrist,cells", [(140, 3), (165, 5), (200, 8)])
def test_band_design_no_shorts_and_fits(wrist, cells):
    b = BandDesign(wrist_mm=wrist, cells=cells)
    assert not b.traces_overlap()
    assert all(p.x + p.w <= b.length_mm - b.overlap_mm + 1e-6 for p in b.pockets)
    m = b.metrics()
    assert m["pen_strokes"] >= 2 + cells and m["pcl_mass_g"] > 0
    svg = b.svg_template()
    assert svg.startswith("<svg") and f'width="{b.length_mm + 20}mm"' in svg  # 1:1 mm 단위
    assert b.svg_tattoo().count("<path") >= 9  # 4 채널 x 2 + 기준


def test_band_design_validates_input():
    with pytest.raises(ValueError):
        BandDesign(wrist_mm=50)


# --- 에이전트 ----------------------------------------------------------------
def _state(t, intent, trusted, soc=0.9, twin=None, sun=30.0, gust=2.0):
    return {"t": t, "activation": 0.6 if intent != "rest" else 0.02,
            "decision": {"intent": intent, "trusted": trusted, "trust": 0.9 if trusted else 0.1},
            "flight": {"airborne": True, "altitude_m": 30.0, "bank_deg": 10.0, "throttle": 0.5},
            "power": {"soc": soc}, "twin": twin or [],
            "env": {"local_time": "2026-11-11 21:00", "sun": {"elevation_deg": sun}, "weather": {"wind_gusts_10m": gust}}}


def test_agent_controls_device_by_muscle_hold_and_locks_for_impostor():
    sent = []
    agent = AlliedAgent(command=lambda d, p: sent.append((d, p)))
    k = QEAAIKernel.build_default(seed=0)
    lamp = [{"id": "desk-lamp", "kind": "actuator", "source": "mqtt", "on": False}]
    for i in range(6):  # 주먹 1.2 초 유지
        agent.step(_state(i * 0.2, "fist", True, twin=lamp), k, 0.2)
    assert sent == [("desk-lamp", {"on": True})]
    lamp[0]["on"] = True
    agent.step(_state(2.0, "fist", False, twin=lamp), k, 0.2)  # 타인 착용
    assert agent.locked and sent[-1] == ("desk-lamp", {"on": False})
    assert any(l["kind"] == "alert" for l in agent.log)


def test_agent_power_saver_and_night_dim_change_real_loads():
    agent = AlliedAgent()
    k = QEAAIKernel.build_default(seed=0)
    agent.step(_state(0, "rest", True, soc=0.2, sun=-20), k, 0.2)
    assert agent.saver and k.power.loads["radio"].duty == pytest.approx(0.05)
    agent.step(_state(0.2, "rest", True, soc=0.8, sun=-20), k, 0.2)
    assert not agent.saver and k.power.loads["display"].power_w == pytest.approx(0.5e-3)  # 밤이라 어둡게
    agent.step(_state(0.4, "rest", True, soc=0.8, sun=20, gust=14), k, 0.2)
    assert k.power.loads["display"].power_w == pytest.approx(0.8e-3) and agent.windy


def test_persona_roundtrip(tmp_path):
    path = tmp_path / "p.json"
    agent = AlliedAgent(persona_path=path)
    k = QEAAIKernel.build_default(seed=0)
    for i in range(30):
        agent.step(_state(i * 0.2, "wrist_right", True), k, 0.2)
    agent.save()
    again = AlliedAgent(persona_path=path)
    s = again.persona.summary()
    assert s["favorite_gesture"] == "wrist_right" and s["peak_hour"] == 21
    assert isinstance(PersonaProfile.from_dict({}), PersonaProfile)


# --- MCP ------------------------------------------------------------------------
@pytest.fixture
def server(monkeypatch):
    sim = Simulation(seed=0)
    sim.step()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(sim))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    import qeaai.agent.mcp_server as mcp
    monkeypatch.setattr(mcp, "BASE", url)
    yield sim, mcp
    httpd.shutdown()
    httpd.server_close()


def test_mcp_protocol_and_tools(server, monkeypatch):
    sim, mcp = server
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_status", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "set_device", "arguments": {"id": "desk-lamp", "properties": {"on": True}}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "set_device", "arguments": {"id": "../x", "properties": {"on": True}}}},
        {"jsonrpc": "2.0", "id": 6, "method": "nope"},
    ]
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(json.dumps(l) for l in lines) + "\n"))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    mcp.main()
    resp = {r["id"]: r for r in map(json.loads, out.getvalue().splitlines())}
    assert resp[1]["result"]["serverInfo"]["name"] == "qeaai"
    assert {t["name"] for t in resp[2]["result"]["tools"]} >= {"get_status", "set_device", "control", "list_things"}
    status = json.loads(resp[3]["result"]["content"][0]["text"])
    assert "soc_pct" in status and "intent" in status
    assert not resp[4]["result"].get("isError")
    assert any("desk-lamp" in l["text"] for l in sim.agent.log)
    assert resp[5]["result"]["isError"]
    assert resp[6]["error"]["code"] == -32601


# --- VPN ------------------------------------------------------------------------
@pytest.mark.skipif(not shutil.which("wg"), reason="wireguard-tools 없음")
def test_vpn_generates_valid_wireguard_configs(tmp_path):
    import base64
    import re
    import stat

    from qeaai.iot.vpn import generate
    r = generate(tmp_path / "vpn", ["esp32-01", "phone"], endpoint="192.0.2.10")
    server = (tmp_path / "vpn" / "wg-qeaai.conf").read_text()
    assert server.count("[Peer]") == 2 and "ListenPort = 51820" in server
    peer = (tmp_path / "vpn" / "peer-phone.conf").read_text()
    assert "Endpoint = 192.0.2.10:51820" in peer and "AllowedIPs = 10.77.0.1/32" in peer
    for text in (server, peer):
        for key in re.findall(r"(?:PrivateKey|PublicKey|PresharedKey) = (\S+)", text):
            assert len(base64.b64decode(key)) == 32
    assert stat.S_IMODE((tmp_path / "vpn" / "peer-phone.conf").stat().st_mode) == 0o600
    with pytest.raises(ValueError):
        generate(tmp_path / "v2", ["bad name!"])

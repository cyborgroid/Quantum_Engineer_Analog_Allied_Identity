import json
import threading
import urllib.error
import urllib.request

import pytest

from qeaai.web.server import WEB_DEMO_LOOP, Simulation, make_handler


def test_demo_loop_takes_off_and_lands():
    sim = Simulation(seed=0)
    total = round(sum(s for _, s in WEB_DEMO_LOOP) / sim.kernel.dt)
    states = [sim.step() for _ in range(total)]
    assert max(s["flight"]["altitude_m"] for s in states) > 20
    assert states[-1]["flight"]["altitude_m"] == 0.0
    assert (states[-1]["flight"]["x"], states[-1]["flight"]["y"]) == (0.0, 0.0)  # 한 바퀴 후 활주로로 복귀
    assert {"band", "flyer", "beacon-n"} <= {d["id"] for d in states[-1]["twin"]}
    assert len(states[-1]["emg"]) == 4


def test_impostor_switch_locks_control():
    sim = Simulation(seed=0)
    sim.control({"wearer": "impostor", "gesture": "fist"})
    states = [sim.step() for _ in range(15)]
    assert not states[-1]["decision"]["trusted"]
    assert states[-1]["flight"]["altitude_m"] == 0.0


def test_control_validation_and_recharge():
    sim = Simulation(seed=0)
    for bad in ({"gesture": "fly"}, {"mode": "x"}, {"wearer": "nobody"}):
        with pytest.raises(ValueError):
            sim.control(bad)
    sim.kernel.power.pack.cell.voltage = 0.1
    sim.control({"recharge": True})
    assert sim.kernel.power.pack.cell.voltage == pytest.approx(0.8)


@pytest.fixture
def server():
    from http.server import ThreadingHTTPServer

    sim = Simulation(seed=0)
    sim.step()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(sim))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_http_endpoints(server):
    assert b"QEAAI OS" in urllib.request.urlopen(server + "/").read()
    assert urllib.request.urlopen(server + "/app.js").headers["Content-Type"].startswith("text/javascript")
    info = json.load(urllib.request.urlopen(server + "/api/info"))
    assert len(info["qe_curve"]) == 41
    assert info["model_url"] is None or info["model_url"].endswith(".glb")
    assert b"three/addons/" in urllib.request.urlopen(server + "/").read()  # Three.js import map
    assert urllib.request.urlopen(server + "/vr3d.js").headers["Content-Type"].startswith("text/javascript")
    state = json.load(urllib.request.urlopen(server + "/api/state"))
    assert "flight" in state
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(server + "/../server.py")
    assert e.value.code == 404

    req = urllib.request.Request(server + "/api/control", data=b'{"gesture":"open"}', method="POST")
    assert json.load(urllib.request.urlopen(req))["gesture"] == "open"
    bad = urllib.request.Request(server + "/api/control", data=b'{"gesture":"nope"}', method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(bad)
    assert e.value.code == 400


def test_demo_auto_recharges():
    sim = Simulation(seed=0)
    sim.kernel.power.pack.cell.voltage = 0.1  # 거의 방전
    state = sim.step()
    assert state["power"]["soc"] > 0.9
    assert "자동 충전" in state["script"]

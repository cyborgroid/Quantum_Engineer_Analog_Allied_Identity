import calendar
import json
import socket
import threading
import time

import pytest

from qeaai.iot import HostSensors, MQTTBroker, MQTTClient, sun_position, thing_description, topic_matches
from qeaai.iot.realworld import RealWorld, RealWorldConfig, parse_thing_message
from qeaai.web.server import Simulation


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.mark.parametrize("filt,topic,ok", [
    ("a/b", "a/b", True), ("a/+", "a/b", True), ("a/+", "a/b/c", False), ("a/#", "a", True),
    ("a/#", "a/b/c", True), ("#", "x/y", True), ("+/b", "a/b", True), ("a/b", "a/c", False),
    ("#", "$SYS/x", False),
])
def test_topic_matching(filt, topic, ok):
    assert topic_matches(filt, topic) is ok


@pytest.fixture
def broker():
    received = []
    b = MQTTBroker("127.0.0.1", 0, on_publish=lambda t, p, c: received.append((t, p, c)))
    b.start(port_tries=1)
    b.received = received
    yield b
    b.stop()


def test_mqtt_pubsub_retained_and_qos1(broker):
    sub = MQTTClient("127.0.0.1", broker.port, client_id="sub")
    got = []
    sub.on_message = lambda t, p: got.append((t, p))
    sub.connect()
    broker.publish("qeaai/twin/x/state", {"v": 1}, retain=True)
    sub.subscribe("qeaai/twin/#")  # retained 메시지를 구독 즉시 받는다
    assert wait_for(lambda: got)
    assert json.loads(got[0][1]) == {"v": 1}

    pub = MQTTClient("127.0.0.1", broker.port, client_id="dev-1")
    pub.connect()
    pub.publish("qeaai/twin/y/state", "hello", qos=1)  # PUBACK 을 기다림
    assert wait_for(lambda: len(got) == 2)
    assert got[1] == ("qeaai/twin/y/state", b"hello")
    assert broker.received[-1] == ("qeaai/twin/y/state", b"hello", "dev-1")
    assert set(broker.clients) == {"sub", "dev-1"}
    pub.disconnect()
    sub.disconnect()


def test_mqtt_rejects_bad_credentials():
    b = MQTTBroker("127.0.0.1", 0, username="u", password="p")
    b.start(port_tries=1)
    try:
        with pytest.raises(ConnectionError):
            MQTTClient("127.0.0.1", b.port, username="u", password="wrong").connect()
        ok = MQTTClient("127.0.0.1", b.port, username="u", password="p")
        ok.connect()
        ok.disconnect()
    finally:
        b.stop()


def test_mqtt_survives_garbage(broker):
    s = socket.create_connection(("127.0.0.1", broker.port))
    s.sendall(b"\x10\xff\xff\xff\xff\x7f garbage")
    s.close()
    c = MQTTClient("127.0.0.1", broker.port)
    c.connect()  # 브로커는 계속 동작
    c.disconnect()


def test_sun_position_matches_astronomy():
    noon = calendar.timegm((2026, 6, 21, 3, 30, 0))  # 서울 하지 남중 무렵 (KST 12:30)
    p = sun_position(37.5665, 126.978, noon)
    assert abs(p["elevation_deg"] - (90 - 37.5665 + 23.44)) < 1.0
    assert 160 < p["azimuth_deg"] < 200
    assert sun_position(37.5665, 126.978, noon + 12 * 3600)["elevation_deg"] < -20


def test_host_sensors_read_real_values():
    h = HostSensors()
    h.read()
    time.sleep(0.2)
    state = h.read()
    assert state["kind"] == "edge_gateway"
    assert "mem_used_pct" in state and 0 < state["mem_used_pct"] < 100


def test_parse_thing_message():
    assert parse_thing_message("qeaai/things/room/state", b'{"temp_c": 23.5, "kind": "env", "bad key": 1}') == (
        "room", {"temp_c": 23.5, "kind": "env"})
    assert parse_thing_message("qeaai/things/room/temp_c", b"21.0") == ("room", {"temp_c": 21.0})
    assert parse_thing_message("qeaai/things/room/on", b"true") == ("room", {"on": True})
    assert parse_thing_message("other/things/room/state", b"{}") is None
    assert parse_thing_message("qeaai/things/../state", b"{}") is None
    assert parse_thing_message("qeaai/things/room/state", b"[1,2]") is None


def test_thing_description_is_wot():
    td = thing_description({"id": "room", "kind": "env", "source": "mqtt", "temp_c": 23.5, "on": True},
                           "http://h", "mqtt://h:1883")
    assert td["@context"][0] == "https://www.w3.org/2022/wot/td/v1.1"
    assert td["id"] == "urn:qeaai:thing:room"
    assert td["properties"]["temp_c"]["unit"] == "degree Celsius"
    assert td["properties"]["on"]["type"] == "boolean"
    assert td["qeaai:reality"] == "measured"


def test_mqtt_device_enters_twin_and_twin_is_published():
    rw = RealWorld(RealWorldConfig(weather=False, mqtt_port=0))
    rw.broker.port = 0
    rw.start()
    try:
        sim = Simulation(seed=0, realworld=rw)
        watcher = MQTTClient("127.0.0.1", rw.broker.port, client_id="watcher")
        seen = {}
        watcher.on_message = lambda t, p: seen.__setitem__(t, json.loads(p))
        watcher.connect()
        watcher.subscribe("qeaai/twin/#")

        dev = MQTTClient("127.0.0.1", rw.broker.port, client_id="esp32-01")
        dev.connect()
        dev.publish("qeaai/things/esp32-01/state", {"kind": "esp32_sensor", "chip_temp_c": 41.5}, qos=1)
        dev.publish("qeaai/things/band/state", {"soc": 0}, qos=1)  # 예약된 id 는 사칭 불가
        assert wait_for(lambda: any(d["id"] == "esp32-01" for d in sim.kernel.twin.scene()))
        state = sim.step()
        thing = next(d for d in state["twin"] if d["id"] == "esp32-01")
        assert thing["real"] and thing["source"] == "mqtt" and thing["chip_temp_c"] == 41.5
        assert "esp32-01" in state["places"]
        band = next(d for d in state["twin"] if d["id"] == "band")
        assert band["source"] == "sim" and band["soc"] > 0
        gw = next(d for d in state["twin"] if d["id"] == "edge-gateway")
        assert gw["real"] and gw["source"] == "sysfs"
        assert wait_for(lambda: "qeaai/twin/esp32-01/state" in seen and "qeaai/twin/flyer/state" in seen)

        sim.control({"reset": True})  # 리셋해도 실제 장치는 트윈에 남는다
        assert any(d["id"] == "esp32-01" for d in sim.kernel.twin.scene())
        dev.disconnect()
        watcher.disconnect()
    finally:
        rw.stop()


def test_real_wind_pushes_aircraft():
    rw = RealWorld(RealWorldConfig(weather=False, mqtt=False, sensors=False))
    rw.env.weather.current = {"wind_speed_10m": 5.0, "wind_direction_10m": 270.0}  # 서풍 -> 동쪽으로 밀림
    sim = Simulation(seed=0, realworld=rw)
    sim.kernel.game.state.altitude_m = 50.0
    sim.kernel.game.state.speed_mps = 15.0
    x0 = sim.x
    sim.step()
    assert sim.x - x0 == pytest.approx(5.0 * sim.kernel.dt, abs=1e-6)

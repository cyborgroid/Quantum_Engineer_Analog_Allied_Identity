"""실제 세계 연결 묶음: 실측 센서 + 실제 환경 + MQTT 브로커.

MQTT 토픽 규약
    장치 -> 트윈 (입력)
        qeaai/things/<id>/state        JSON 객체   예) {"kind":"temp_sensor","temp_c":23.4,"x":-90,"y":30}
        qeaai/things/<id>/<property>   단일 값     예) qeaai/things/desk/temp_c  ->  23.4
    트윈 -> 바깥 (출력, retained)
        qeaai/twin/<id>/state          트윈에 있는 모든 장치의 최신 상태
        qeaai/neural/intent            Allied Identity 가 해석한 근육 의도
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .environment import Location, RealEnvironment
from .mqtt import MQTTBroker
from .sensors import HostSensors

THING_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")
PROP_NAME = re.compile(r"^[A-Za-z0-9_]{1,40}$")
MAX_THINGS = 64
MAX_PROPS = 24


@dataclass
class RealWorldConfig:
    location: Location = field(default_factory=Location)
    weather: bool = True
    sensors: bool = True
    mqtt: bool = True
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    mqtt_user: str | None = None
    mqtt_password: str | None = None


class RealWorld:
    def __init__(self, cfg: RealWorldConfig | None = None):
        self.cfg = cfg or RealWorldConfig()
        self.env = RealEnvironment(self.cfg.location, weather=self.cfg.weather)
        sensors = HostSensors() if self.cfg.sensors else None
        self.sensors = sensors if sensors and sensors.available else None
        self.broker = MQTTBroker(self.cfg.mqtt_host, self.cfg.mqtt_port, self.cfg.mqtt_user, self.cfg.mqtt_password) if self.cfg.mqtt else None
        self.mqtt_error: str | None = None

    def start(self) -> None:
        self.env.start()
        if self.broker:
            try:
                self.broker.start()
            except OSError as e:
                self.mqtt_error = str(e)
                self.broker = None

    def stop(self) -> None:
        self.env.weather.stop()
        if self.broker:
            self.broker.stop()

    def weather_state(self) -> dict | None:
        w = self.env.weather.current
        if not w:
            return None
        return {
            "kind": "weather_station",
            "source": "open-meteo",
            "place": self.cfg.location.name,
            "description": w.get("description"),
            "temp_c": w.get("temperature_2m"),
            "feels_like_c": w.get("apparent_temperature"),
            "humidity_pct": w.get("relative_humidity_2m"),
            "wind_mps": w.get("wind_speed_10m"),
            "wind_dir_deg": w.get("wind_direction_10m"),
            "gust_mps": w.get("wind_gusts_10m"),
            "cloud_pct": w.get("cloud_cover"),
            "pressure_hpa": w.get("pressure_msl"),
            "precip_mm": w.get("precipitation"),
            "is_day": bool(w.get("is_day")),
            "observed": w.get("time"),
        }

    def gateway_state(self) -> dict | None:
        if not self.sensors:
            return None
        from .vpn import status as vpn_status
        return {**self.sensors.read(), **vpn_status(), "source": "sysfs"}

    def status(self) -> dict:
        b = self.broker
        return {
            "enabled": b is not None,
            "url": b.url if b else None,
            "error": self.mqtt_error,
            "auth": bool(b and b.username is not None),
            "clients": b.clients if b else [],
            **({"in": b.stats["in"], "out": b.stats["out"], "dropped": b.stats["dropped"]} if b else {}),
        }


def parse_thing_message(topic: str, payload: bytes) -> tuple[str, dict] | None:
    """MQTT 입력 토픽 -> (thing id, 속성 dict). 규약에 맞지 않으면 None."""
    parts = topic.split("/")
    if len(parts) != 4 or parts[0] != "qeaai" or parts[1] != "things" or not THING_ID.match(parts[2]):
        return None
    tid, leaf = parts[2], parts[3]
    text = payload.decode("utf-8", "replace").strip()
    if leaf == "state":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
    elif PROP_NAME.match(leaf):
        data = {leaf: _scalar(text)}
    else:
        return None
    clean: dict = {}
    for k, v in data.items():
        if len(clean) >= MAX_PROPS or not isinstance(k, str) or not PROP_NAME.match(k) or k == "source":
            continue
        if isinstance(v, bool) or (isinstance(v, (int, float)) and v == v and abs(v) < 1e12):
            clean[k] = v
        elif isinstance(v, str):
            clean[k] = v[:64]
    return tid, clean


def _scalar(text: str):
    low = text.lower()
    if low in ("true", "on"):
        return True
    if low in ("false", "off"):
        return False
    try:
        return float(text)
    except ValueError:
        return text[:64]

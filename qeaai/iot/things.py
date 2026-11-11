"""디지털 트윈 표준 기술: W3C Web of Things (WoT) Thing Description 1.1.

트윈에 있는 모든 장치(실측/가상)를 표준 JSON-LD 로 기술한다. Eclipse Ditto, Azure Digital Twins,
node-wot 같은 도구가 이 기술을 읽어 장치를 발견하고 속성을 HTTP / MQTT 로 읽을 수 있다.
https://www.w3.org/TR/wot-thing-description11/
"""

from __future__ import annotations

from typing import Any

TD_CONTEXT = ["https://www.w3.org/2022/wot/td/v1.1", {"qeaai": "urn:qeaai:"}]

UNITS = {
    "_c": "degree Celsius", "_pct": "percent", "_w": "watt", "_v": "volt", "_m": "metre",
    "_kbps": "kilobit per second", "_h": "hour", "_mps": "metre per second", "_uw": "microwatt",
    "_hpa": "hectopascal", "_deg": "degree",
}
TITLES = {
    "edge_gateway": "엣지 게이트웨이 (이 컴퓨터)",
    "weather_station": "기상 관측 (Open-Meteo)",
    "cyborg_band": "Allied Identity 손목밴드",
    "game": "비행체",
    "iot_beacon": "IoT 비콘",
    "mqtt_device": "MQTT 장치",
}
REALITY = {"sim": "simulated", "sysfs": "measured", "open-meteo": "measured", "mqtt": "measured"}


def _schema(name: str, value: Any) -> dict:
    if isinstance(value, bool):
        s: dict = {"type": "boolean"}
    elif isinstance(value, (int, float)):
        s = {"type": "number"}
    elif isinstance(value, dict):
        s = {"type": "object"}
    else:
        s = {"type": "string"}
    lname = name.lower()
    for suffix, unit in UNITS.items():
        if lname.endswith(suffix):
            s["unit"] = unit
            break
    return s


def thing_description(device: dict, base_url: str, mqtt_url: str | None = None) -> dict:
    """트윈 scene 항목 하나 -> WoT TD."""
    tid = device["id"]
    kind = device.get("kind", "unknown")
    source = device.get("source", "sim")
    skip = {"id", "kind", "source", "age_s", "real"}
    props = {}
    for name, value in device.items():
        if name in skip:
            continue
        forms = [{"href": f"{base_url}/api/things/{tid}/properties/{name}", "op": "readproperty",
                  "contentType": "application/json"}]
        if mqtt_url:
            forms.append({"href": f"{mqtt_url}/qeaai/twin/{tid}/state", "op": "observeproperty",
                          "contentType": "application/json"})
        props[name] = {**_schema(name, value), "readOnly": True, "observable": True, "forms": forms}
    return {
        "@context": TD_CONTEXT,
        "@type": f"qeaai:{kind}",
        "id": f"urn:qeaai:thing:{tid}",
        "title": f"{TITLES.get(kind, kind)} · {tid}",
        "description": f"source={source}, data={REALITY.get(source, 'measured')}",
        "qeaai:source": source,
        "qeaai:reality": REALITY.get(source, "measured"),
        "securityDefinitions": {"nosec_sc": {"scheme": "nosec"}},
        "security": "nosec_sc",
        "properties": props,
        "links": [{"rel": "item", "href": f"{base_url}/api/things/{tid}/properties", "type": "application/json"}],
    }

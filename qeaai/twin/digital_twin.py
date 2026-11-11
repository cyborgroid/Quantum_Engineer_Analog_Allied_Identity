"""VR = 확장된 현실 = IoT + 디지털 트윈.

네트워크 버스의 'device/<id>/state' 토픽을 구독해서 모든 장치 상태를 거울처럼 유지한다.
각 장치는 데이터 출처(source)를 가진다:
    sim         시뮬레이션 (손목밴드, 비행체, 가상 비콘)
    sysfs       이 컴퓨터의 실측 센서 (엣지 게이트웨이)
    open-meteo  실제 날씨
    mqtt        MQTT 로 접속한 실제 장치
VR 뷰는 이 트윈을 그려 주는 것뿐이다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..network.bus import Message, NetworkBus

META_KEYS = ("kind", "source")


@dataclass
class DeviceTwin:
    device_id: str
    kind: str
    source: str = "sim"
    state: dict[str, Any] = field(default_factory=dict)
    updates: int = 0
    updated_at: float = 0.0

    @property
    def real(self) -> bool:
        return self.source != "sim"


class DigitalTwin:
    def __init__(self, bus: NetworkBus):
        self.devices: dict[str, DeviceTwin] = {}
        bus.subscribe("device/*/state", self._on_state)

    def register(self, device_id: str, kind: str, source: str = "sim") -> DeviceTwin:
        twin = self.devices.setdefault(device_id, DeviceTwin(device_id, kind, source))
        twin.kind, twin.source = kind, source
        return twin

    def _on_state(self, msg: Message) -> None:
        device_id = msg.topic.split("/")[1]
        p = msg.payload
        twin = self.devices.get(device_id) or self.register(device_id, p.get("kind", "unknown"), p.get("source", "sim"))
        if "kind" in p:
            twin.kind = p["kind"]
        if "source" in p:
            twin.source = p["source"]
        twin.state.update({k: v for k, v in p.items() if k not in META_KEYS})
        twin.updates += 1
        twin.updated_at = time.time()

    def scene(self) -> list[dict[str, Any]]:
        now = time.time()
        devices = sorted(self.devices.values(), key=lambda d: (not d.real, d.device_id))
        return [
            {"id": d.device_id, "kind": d.kind, "source": d.source, "real": d.real,
             "age_s": round(now - d.updated_at, 1) if d.updated_at else None, **d.state}
            for d in devices
        ]

    def render_vr(self) -> str:
        lines = []
        for d in self.devices.values():
            fields = ", ".join(f"{k}={_fmt(v)}" for k, v in d.state.items())
            lines.append(f"  [{'REAL' if d.real else 'SIM '}] [{d.kind}:{d.device_id}] {fields}")
        return "\n".join(lines) if lines else "  (no devices)"


def _fmt(v: Any) -> str:
    return f"{v:.2f}" if isinstance(v, float) else str(v)

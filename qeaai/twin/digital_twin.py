"""VR = 확장된 현실 = IoT + 디지털 트윈.

네트워크 버스의 'device/<id>/state' 토픽을 구독해서 모든 실물 디바이스의
상태를 거울처럼 유지한다. VR 뷰는 이 트윈을 그려 주는 것뿐이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..network.bus import Message, NetworkBus


@dataclass
class DeviceTwin:
    device_id: str
    kind: str
    state: dict[str, Any] = field(default_factory=dict)
    updates: int = 0


class DigitalTwin:
    def __init__(self, bus: NetworkBus):
        self.devices: dict[str, DeviceTwin] = {}
        bus.subscribe("device/*/state", self._on_state)

    def register(self, device_id: str, kind: str) -> DeviceTwin:
        twin = self.devices.setdefault(device_id, DeviceTwin(device_id, kind))
        twin.kind = kind
        return twin

    def _on_state(self, msg: Message) -> None:
        device_id = msg.topic.split("/")[1]
        twin = self.devices.get(device_id) or self.register(device_id, msg.payload.get("kind", "unknown"))
        twin.state.update({k: v for k, v in msg.payload.items() if k != "kind"})
        twin.updates += 1

    def scene(self) -> list[dict[str, Any]]:
        return [{"id": d.device_id, "kind": d.kind, **d.state} for d in self.devices.values()]

    def render_vr(self) -> str:
        lines = []
        for d in self.devices.values():
            fields = ", ".join(f"{k}={_fmt(v)}" for k, v in d.state.items())
            lines.append(f"  [{d.kind}:{d.device_id}] {fields}")
        return "\n".join(lines) if lines else "  (no devices)"


def _fmt(v: Any) -> str:
    return f"{v:.2f}" if isinstance(v, float) else str(v)

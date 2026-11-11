"""OS - "눈으로 보고 경험하는 것들이 모두 OS 이다".

한 틱(기본 200 ms)마다:
    1. 근육 신호 읽기            (bio)
    2. 근육 움직임으로 발전      (bio.harvester -> circuit)
    3. 의도 해석 + 신원 확인     (identity)
    4. 의도 방송                 (network)
    5. 비행 게임 조종            (game)
    6. 디바이스 전력 소비        (circuit <- materials)
    7. 디바이스 상태 방송 -> 트윈 (network -> twin = VR)
    8. 화면 렌더링               (system.render)
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Protocol

from ..bio.emg import EMGSimulator, UserPhysiology, Window, activation_level, extract_features
from ..bio.harvester import MuscleHarvester
from ..circuit.power import Load, PowerBus, PowerReport
from ..game.flight import FlightGame, FlightState
from ..identity.allied_identity import AlliedIdentity, Decision, collect_calibration
from ..materials.snss_battery import SNSSPack
from ..network.bus import NetworkBus, SecureChannel
from ..twin.digital_twin import DigitalTwin


class SignalSource(Protocol):
    def read(self, duration_s: float) -> Window: ...


# (동작, 초) - 이륙, 우선회, 좌선회, 감속
DEMO_SCRIPT: list[tuple[str, float]] = [
    ("rest", 2.0),
    ("fist", 6.0),
    ("rest", 3.0),
    ("wrist_right", 2.0),
    ("rest", 4.0),
    ("wrist_left", 2.0),
    ("rest", 3.0),
    ("open", 6.0),
    ("rest", 6.0),
]


@dataclass(frozen=True)
class Frame:
    t: float
    truth: str | None
    activation: float
    decision: Decision | None
    power: PowerReport
    flight: FlightState


class QEAAIKernel:
    def __init__(
        self,
        source: SignalSource,
        identity: AlliedIdentity,
        power: PowerBus,
        bus: NetworkBus,
        harvester: MuscleHarvester | None = None,
        dt: float = 0.2,
    ):
        self.source = source
        self.identity = identity
        self.power = power
        self.bus = bus
        self.harvester = harvester or MuscleHarvester()
        self.dt = dt
        self.twin = DigitalTwin(bus)
        self.game = FlightGame()
        self.t = 0.0
        self.twin.register("band", "cyborg_band")
        self.twin.register("flyer", "game")
        self.identity.on_worn()

    # --- 기본 구성 -------------------------------------------------------
    @classmethod
    def build_default(cls, seed: int = 0, wearer_seed: int | None = None) -> "QEAAIKernel":
        """seed 의 사용자와 페어링. wearer_seed 가 다르면 다른 사람이 착용한 상황."""
        owner = UserPhysiology.random("owner", seed)
        identity = AlliedIdentity()
        identity.pair(owner.user_id, collect_calibration(EMGSimulator(owner, seed=seed + 1)))

        wearer = owner if wearer_seed is None else UserPhysiology.random("wearer", wearer_seed)
        source = EMGSimulator(wearer, seed=seed + 2)

        power = PowerBus(SNSSPack.charged())
        power.add_load(Load("core_mcu", 150e-6))
        power.add_load(Load("display", 800e-6))
        power.add_load(Load("radio", 1.5e-3, duty=0.2))

        bus = NetworkBus(SecureChannel(secrets.token_bytes(32)))
        return cls(source, identity, power, bus)

    # --- 실행 -------------------------------------------------------------
    def tick(self, truth: str | None = None) -> Frame:
        if truth is not None and hasattr(self.source, "set_gesture"):
            self.source.set_gesture(truth)

        window = self.source.read(self.dt)
        activation = activation_level(window)
        harvest_w = self.harvester.power(activation)

        decision: Decision | None = None
        report = self.power.step(self.dt, harvest_w)
        if not report.brownout:
            decision = self.identity.decide(extract_features(window))
            self.bus.publish(
                "neural/intent",
                {"intent": decision.intent, "confidence": decision.confidence, "trusted": decision.trusted},
                sender="allied_identity",
            )

        controlling = decision is not None and decision.trusted
        flight = self.game.apply(decision.intent if controlling else None, decision.confidence if controlling else 0.0, self.dt)

        self.bus.publish(
            "device/band/state",
            {"kind": "cyborg_band", "pack_v": report.pack_voltage, "soc": report.state_of_charge,
             "harvest_uW": report.harvest_w * 1e6, "brownout": report.brownout},
            sender="band",
        )
        self.bus.publish(
            "device/flyer/state",
            {"kind": "game", "alt_m": flight.altitude_m, "speed": flight.speed_mps,
             "heading": flight.heading_deg, "airborne": flight.airborne},
            sender="flyer",
        )

        self.t += self.dt
        return Frame(self.t, truth, activation, decision, report, FlightState(**vars(flight)))

    def run(self, script: list[tuple[str, float]]) -> list[Frame]:
        frames = []
        for gesture, seconds in script:
            for _ in range(max(1, round(seconds / self.dt))):
                frames.append(self.tick(gesture))
        return frames

    # --- 화면 (OS) --------------------------------------------------------
    def render(self, frame: Frame) -> str:
        d = frame.decision
        p = frame.power
        f = frame.flight
        bar = "#" * round(p.state_of_charge * 20)
        if d is None:
            ai_line = "AI     : -- 전원 부족 (brownout) --"
        else:
            status = "CONTROL" if d.trusted else "LOCKED (페어링된 사용자 아님)"
            ai_line = f"AI     : intent={d.intent:<11} conf={d.confidence:4.2f} trust={d.trust:4.2f}  {status}"
        return "\n".join([
            f"== QEAAI OS  t={frame.t:6.1f}s  owner={self.identity.paired_user} ==",
            f"MUSCLE : truth={frame.truth or '?':<11} activation={frame.activation:4.2f}",
            ai_line,
            f"POWER  : pack={p.pack_voltage:4.2f}V  SOC [{bar:<20}] {p.state_of_charge*100:5.1f}%"
            f"  load={p.load_w*1e3:4.2f}mW  harvest={p.harvest_w*1e6:5.1f}uW",
            f"FLIGHT : {'AIRBORNE' if f.airborne else 'GROUND  '} alt={f.altitude_m:6.1f}m"
            f"  speed={f.speed_mps:5.1f}m/s  heading={f.heading_deg:5.1f}  bank={f.bank_deg:+5.1f}",
            "VR/TWIN:",
            self.twin.render_vr(),
        ])

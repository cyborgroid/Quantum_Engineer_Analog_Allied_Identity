from dataclasses import replace

import pytest

from qeaai.cli import main
from qeaai.game import FlightGame
from qeaai.network import Message, NetworkBus, SecureChannel
from qeaai.system import DEMO_SCRIPT, QEAAIKernel


def test_bus_rejects_tampered_messages():
    key = b"k" * 32
    sender = NetworkBus(SecureChannel(key))
    receiver = NetworkBus(SecureChannel(key))
    got = []
    receiver.subscribe("neural/*", got.append)

    msg = sender.publish("neural/intent", {"intent": "fist"}, sender="ai")
    assert receiver.receive(Message.from_json(msg.to_json()))
    assert not receiver.receive(replace(msg, payload={"intent": "open"}))
    assert not NetworkBus(SecureChannel(b"x" * 32)).receive(msg)
    assert len(got) == 1 and receiver.rejected == 1


def test_fist_makes_it_fly():
    game = FlightGame()
    for _ in range(50):
        game.apply("fist", 1.0, 0.2)
    assert game.state.airborne


def test_no_control_is_failsafe():
    game = FlightGame()
    game.state.throttle = 1.0
    for _ in range(25):
        game.apply(None, 0.0, 0.2)
    assert game.state.throttle == 0.0


def test_owner_flies_end_to_end():
    kernel = QEAAIKernel.build_default(seed=0)
    frames = kernel.run(DEMO_SCRIPT)
    assert max(f.flight.altitude_m for f in frames) > 20
    assert all(f.decision is not None for f in frames)
    accuracy = sum(f.decision.intent == f.truth for f in frames) / len(frames)
    assert accuracy > 0.9
    twin = {d["id"]: d for d in kernel.twin.scene()}
    assert twin["flyer"]["alt_m"] == pytest.approx(frames[-1].flight.altitude_m)
    assert "QEAAI OS" in kernel.render(frames[-1])


@pytest.mark.parametrize("seed", range(4))
def test_impostor_cannot_fly(seed):
    kernel = QEAAIKernel.build_default(seed=seed, wearer_seed=seed + 999)
    frames = kernel.run(DEMO_SCRIPT)
    assert max(f.flight.altitude_m for f in frames) == 0.0


@pytest.mark.parametrize("argv", [["battery"], ["identity", "--impostors", "2"], ["demo"]])
def test_cli_commands(argv, capsys):
    assert main(argv) == 0
    assert capsys.readouterr().out

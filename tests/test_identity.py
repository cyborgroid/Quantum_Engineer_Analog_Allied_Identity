import pytest

from qeaai.bio import GESTURES, EMGSimulator, UserPhysiology, extract_features
from qeaai.identity import AlliedIdentity, NotPairedError, collect_calibration


def stream(phys, seed, n=20, skip_rest=False):
    sim = EMGSimulator(phys, seed=seed)
    for g in GESTURES:
        if skip_rest and g == "rest":
            continue
        sim.set_gesture(g)
        for _ in range(n):
            yield g, extract_features(sim.read())


@pytest.fixture
def paired():
    owner = UserPhysiology.random("owner", 0)
    ai = AlliedIdentity()
    ai.pair("owner", collect_calibration(EMGSimulator(owner, seed=1)))
    return owner, ai


def test_not_complete_without_human():
    with pytest.raises(NotPairedError):
        AlliedIdentity().decide([0.0] * 12)


def test_owner_accuracy(paired):
    owner, ai = paired
    assert ai.evaluate(list(stream(owner, seed=2))) >= 0.95


@pytest.mark.parametrize("other_seed", range(1000, 1005))
def test_impostor_loses_control(paired, other_seed):
    _, ai = paired
    ai.on_worn()
    other = UserPhysiology.random("other", other_seed)
    for _, f in stream(other, seed=other_seed, n=5, skip_rest=True):
        d = ai.decide(f)
    assert not d.trusted


def test_owner_keeps_control(paired):
    owner, ai = paired
    ai.on_worn()
    decisions = [ai.decide(f) for _, f in stream(owner, seed=3, skip_rest=True)]
    assert sum(d.trusted for d in decisions) / len(decisions) >= 0.95


def test_profile_roundtrip(paired, tmp_path):
    owner, ai = paired
    path = tmp_path / "profile.json"
    ai.save(path)
    loaded = AlliedIdentity.load(path)
    assert loaded.paired_user == "owner"
    test = list(stream(owner, seed=4))
    assert loaded.evaluate(test) == pytest.approx(ai.evaluate(test))

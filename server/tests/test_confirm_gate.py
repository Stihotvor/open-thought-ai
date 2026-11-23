"""The confirm gate (hub/confirm.py): one held call, phases held, armed, claimed."""

from ota_server.hub.confirm import ConfirmGate, Held


def held(turn: int = 3, call_id: str = "c1") -> Held:
    return Held("device__set_volume", {"level": 30}, call_id, turn)


def test_phases_follow_hold_arm_claim_pop() -> None:
    gate, h = ConfirmGate(), held(3)
    assert gate.hold(h)
    assert gate.holding_turn() == 3
    assert gate.arm(3) is h
    assert gate.claim(4)
    assert gate.claimed_by(4)
    assert gate.pop() is h
    assert gate.holding_turn() is None
    assert not gate.claimed_by(4)
    assert gate.arm(3) is None


def test_second_hold_is_refused() -> None:
    gate, first = ConfirmGate(), held(3, "c1")
    gate.hold(first)
    assert not gate.hold(held(3, "c2"))
    assert gate.arm(3) is first


def test_pop_is_single_use() -> None:
    gate, h = ConfirmGate(), held(3)
    gate.hold(h)
    gate.arm(3)
    gate.claim(4)
    assert gate.pop() is h
    assert gate.pop() is None


def test_expire_acts_only_on_same_armed_held() -> None:
    gate, a, b = ConfirmGate(), held(3, "c1"), held(3, "c1")
    gate.hold(a)
    token_a = gate.arm(3)
    assert token_a is a
    gate.drop()
    gate.hold(b)
    assert gate.arm(3) is b
    assert not gate.expire(a)
    assert gate.claim(4)
    assert gate.pop() is b


def test_turn_wrap_older_held_does_not_expire() -> None:
    gate, old, new = ConfirmGate(), held(0, "c1"), held(0, "c2")
    gate.hold(old)
    gate.arm(0)
    gate.drop()
    gate.hold(new)
    gate.arm(0)
    assert not gate.expire(old)
    assert gate.expire(new)
    assert gate.pop() is None


def test_drop_if_held_spares_armed_and_claimed() -> None:
    gate, h = ConfirmGate(), held(3)
    gate.hold(h)
    gate.arm(3)
    gate.drop_if_held(3)
    assert gate.expire(h) is True
    gate.hold(h)
    gate.arm(3)
    gate.claim(3)
    gate.drop_if_held(3)
    assert gate.pop() is h


def test_drop_if_held_drops_held_of_that_turn_only() -> None:
    gate, h = ConfirmGate(), held(3)
    gate.hold(h)
    gate.drop_if_held(4)
    assert gate.holding_turn() == 3
    gate.drop_if_held(3)
    assert gate.holding_turn() is None


def test_drop_if_claimed_binds_to_claiming_turn() -> None:
    gate, h = ConfirmGate(), held(3)
    gate.hold(h)
    gate.arm(3)
    gate.claim(5)
    gate.drop_if_claimed(6)
    assert gate.claimed_by(5)
    gate.drop_if_claimed(5)
    assert not gate.claimed_by(5)
    assert gate.pop() is None


def test_arm_and_claim_need_the_right_phase() -> None:
    gate, h = ConfirmGate(), held(3)
    assert gate.arm(3) is None
    assert not gate.claim(4)
    gate.hold(h)
    assert gate.arm(4) is None
    assert not gate.claim(4)
    assert gate.pop() is None
    gate.arm(3)
    assert gate.pop() is None
    assert gate.holding_turn() is None


def test_holding_returns_the_call_only_in_phase_held() -> None:
    gate, h = ConfirmGate(), held(3)
    assert gate.holding() is None
    gate.hold(h)
    assert gate.holding() is h
    gate.arm(3)
    assert gate.holding() is None
    gate.claim(4)
    assert gate.holding() is None


def test_release_drops_only_the_same_claimed_call() -> None:
    gate = ConfirmGate()
    gate.hold(Held("t", {}, "c1", 3))
    token = gate.arm(3)
    assert token is not None
    assert not gate.release(token)  # armed, not claimed
    assert gate.claim(4) is token
    assert not gate.release(Held("t", {}, "c1", 3))  # equal fields, another object
    assert gate.release(token)
    assert not gate.claimed_by(4)
    assert not gate.release(token)  # already gone

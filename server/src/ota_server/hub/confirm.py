"""The confirm gate: one held tool call that outlives its turn and runs at most once.

Phases: held (the step held it), armed (the question played, the timer runs),
claimed (a new turn listens for the answer). Turn ids wrap at 256, so every
check binds to the `Held` object itself, never to its turn alone. No I/O.
"""

from dataclasses import dataclass
from typing import Any, Literal

type Phase = Literal["held", "armed", "claimed"]


@dataclass(frozen=True, eq=False)
class Held:
    """A held call. `eq=False`: two calls with the same fields stay distinct."""

    name: str
    args: dict[str, Any]
    call_id: str
    turn: int


class ConfirmGate:
    """Holds at most one `Held` and its phase."""

    def __init__(self) -> None:
        self._held: Held | None = None
        self._phase: Phase = "held"
        self._claimer: int | None = None

    def hold(self, held: Held) -> bool:
        """Hold `held`; False when a call is already held."""
        if self._held is not None:
            return False
        self._held, self._phase, self._claimer = held, "held", None
        return True

    def arm(self, turn: int) -> Held | None:
        """Arm the call held for `turn`; the `Held` is the token for `expire`."""
        if self._held is None or self._phase != "held" or self._held.turn != turn:
            return None
        self._phase = "armed"
        return self._held

    def expire(self, token: Held) -> bool:
        """Drop the call if `token` is the same object and still armed."""
        if self._held is not token or self._phase != "armed":
            return False
        self.drop()
        return True

    def claim(self, turn: int) -> bool:
        """An armed call becomes claimed by `turn`."""
        if self._held is None or self._phase != "armed":
            return False
        self._phase, self._claimer = "claimed", turn
        return True

    def pop(self) -> Held | None:
        """Return a claimed call and empty the gate; None in any other phase."""
        if self._held is None or self._phase != "claimed":
            return None
        held = self._held
        self.drop()
        return held

    def drop(self) -> None:
        """Empty the gate in any phase."""
        self._held, self._phase, self._claimer = None, "held", None

    def drop_if_claimed(self, turn: int) -> None:
        """Drop only a call claimed by `turn`."""
        if self.claimed_by(turn):
            self.drop()

    def drop_if_held(self, turn: int) -> None:
        """Drop only a call in phase held whose turn is `turn`."""
        if self.holding_turn() == turn:
            self.drop()

    def holding_turn(self) -> int | None:
        """The turn of a call in phase held, else None."""
        if self._held is None or self._phase != "held":
            return None
        return self._held.turn

    def claimed_by(self, turn: int) -> bool:
        """True when a call is claimed by `turn`."""
        return self._held is not None and self._phase == "claimed" and self._claimer == turn

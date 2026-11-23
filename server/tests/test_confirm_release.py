"""The confirmation turn: a yes runs the held call once, anything else cancels (T6, D6, D7)."""

import asyncio
import logging
from typing import Any

import pytest

from ota_server.hub.confirm import Held
from ota_server.hub.hub import McpHub, TurnTools
from ota_server.hub.types import CallCtx, ToolContext, ToolOutcome, ToolSpec

SCHEMA: dict[str, Any] = {"type": "object", "properties": {"level": {"type": "integer"}}}
ARGS: dict[str, Any] = {"level": 3}


class CountingSource:
    """A fake ToolSource that counts calls and returns `result`."""

    timeout = 1.0

    def __init__(self, result: ToolOutcome | None = None, name: str = "device") -> None:
        self.name = name
        self.result = result or ToolOutcome(True, "done")
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def specs(self) -> list[ToolSpec]:
        return [ToolSpec(self.name, "set_volume", "a tool", SCHEMA, confirm=True)]

    async def call(self, tool: str, args: dict[str, Any]) -> ToolOutcome:
        self.calls.append((tool, args))
        return self.result


class DisconnectedSource(CountingSource):
    """A source whose session is gone: the call raises."""

    async def call(self, tool: str, args: dict[str, Any]) -> ToolOutcome:
        self.calls.append((tool, args))
        raise ConnectionError("session closed")


def held(turn: int = 3, source: str = "device") -> Held:
    return Held(f"{source}__set_volume", dict(ARGS), "c1", turn)


def claimed_hub(
    source: CountingSource | None = None, language: str = "en", claimer: int = 4
) -> tuple[McpHub, CountingSource]:
    """A hub with a call held in turn 3, armed and claimed by `claimer`."""
    source = source or CountingSource()
    hub = McpHub([source], language)
    assert hub.gate.hold(held(3, source.name))
    assert hub.gate.arm(3) is not None
    assert hub.gate.claim(claimer)
    return hub, source


def confirm(tools: TurnTools, text: str) -> str | None:
    return asyncio.run(tools.confirmation(text))


def test_yes_runs_the_held_call_once() -> None:
    hub, source = claimed_hub()
    assert confirm(hub.turn_context(4), "yes") == "Done."
    assert source.calls == [("set_volume", ARGS)]


def test_second_yes_runs_nothing() -> None:
    hub, source = claimed_hub()
    tools = hub.turn_context(4)
    confirm(tools, "yes")
    assert confirm(tools, "yes") is None
    assert len(source.calls) == 1


@pytest.mark.parametrize("text", ["no", "yes but later", ""])
def test_non_yes_cancels(text: str) -> None:
    hub, source = claimed_hub()
    assert confirm(hub.turn_context(4), text) == "Cancelled."
    assert source.calls == []
    assert not hub.gate.claimed_by(4)


def test_not_claimed_by_this_turn() -> None:
    hub, source = claimed_hub()
    assert confirm(hub.turn_context(9), "yes") is None
    assert source.calls == []
    assert hub.gate.claimed_by(4)


@pytest.mark.parametrize(
    "source", [CountingSource(ToolOutcome(False, "nope", "tool_error")), DisconnectedSource()]
)
def test_failing_source_gives_the_failure_text(source: CountingSource) -> None:
    hub, _ = claimed_hub(source)
    assert confirm(hub.turn_context(4), "yes") == "That did not work."
    assert len(source.calls) == 1


def test_bug_fails_closed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    hub, source = claimed_hub()

    async def broken(_held: Held) -> ToolOutcome:
        raise RuntimeError("bug")

    monkeypatch.setattr(hub, "_release", broken)
    with caplog.at_level(logging.ERROR, logger="ota_server.hub.hub"):
        assert confirm(hub.turn_context(4), "yes") == "Cancelled."
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors and errors[0].exc_info is not None
    assert not hub.gate.claimed_by(4) and hub.gate.holding_turn() is None
    assert source.calls == []


def test_unknown_language_cancels_and_never_raises() -> None:
    hub, source = claimed_hub(language="xx")
    assert confirm(hub.turn_context(4), "yes") == "Cancelled."
    assert source.calls == []
    assert not hub.gate.claimed_by(4)


def test_cancelled_error_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    hub, _ = claimed_hub()

    async def cancelled(_held: Held) -> ToolOutcome:
        raise asyncio.CancelledError

    monkeypatch.setattr(hub, "_release", cancelled)
    with pytest.raises(asyncio.CancelledError):
        confirm(hub.turn_context(4), "yes")


def test_held_question_only_for_the_holding_turn() -> None:
    hub = McpHub([CountingSource()])
    assert hub.gate.hold(held(3))
    question = hub.turn_context(3).held_question()
    assert question is not None and "device set_volume" in question
    assert hub.turn_context(4).held_question() is None


def test_held_question_none_when_armed_or_empty() -> None:
    hub = McpHub([CountingSource()])
    assert hub.turn_context(3).held_question() is None
    hub.gate.hold(held(3))
    hub.gate.arm(3)
    assert hub.turn_context(3).held_question() is None


def test_release_does_not_count() -> None:
    hub, source = claimed_hub()
    ctx = CallCtx(calls=4, max_calls=4, turn=4)
    tools = TurnTools(hub, ctx)
    assert confirm(tools, "yes") == "Done."
    assert source.calls == [("set_volume", ARGS)]
    assert ctx.calls == 4


def test_release_is_private() -> None:
    members = set(vars(ToolContext))
    assert "_release" not in members
    assert "run" in members


def test_hub_language_is_public_and_read_only() -> None:
    hub = McpHub([CountingSource()], "pl")
    assert hub.language == "pl"
    with pytest.raises(AttributeError):
        hub.language = "en"  # type: ignore[misc]


def test_unknown_language_question_is_english() -> None:
    hub = McpHub([CountingSource()], "xx")
    assert hub.gate.hold(held(3))
    assert (
        hub.turn_context(3).held_question() == "Should I run device set_volume? Say yes to confirm."
    )

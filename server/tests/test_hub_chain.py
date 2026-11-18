"""Hub chain: resolve, limit, validate and wrap (T1)."""

import asyncio
from typing import Any

import pytest

from ota_server.hub.chain import build_chain
from ota_server.hub.steps import UNTRUSTED_PREFIX, limit, resolve, validate, wrap
from ota_server.hub.types import (
    MAX_RESULT_CHARS,
    CallCtx,
    Next,
    Step,
    ToolCall,
    ToolOutcome,
    ToolSpec,
)

VOLUME = ToolSpec(
    source="device",
    name="set_volume",
    description="Set the speaker volume.",
    schema={
        "type": "object",
        "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 100}},
        "required": ["level"],
        "additionalProperties": False,
    },
)
SPECS = {VOLUME.full_name: VOLUME}


class FakeTerminal:
    def __init__(self, text: str = "done") -> None:
        self.calls: list[ToolCall] = []
        self.text = text
        self.log: list[str] | None = None

    async def __call__(self, call: ToolCall, ctx: CallCtx) -> ToolOutcome:
        self.calls.append(call)
        if self.log is not None:
            self.log.append("terminal")
        return ToolOutcome(True, self.text)


def chain(terminal: FakeTerminal, extra: list[Step] | None = None) -> Next:
    steps: list[Step] = [resolve(SPECS), limit(), validate(SPECS), *(extra or []), wrap()]
    return build_chain(steps, terminal)


def run(next_: Next, call: ToolCall, ctx: CallCtx | None = None) -> ToolOutcome:
    async def go() -> ToolOutcome:
        return await next_(call, ctx or CallCtx())

    return asyncio.run(go())


def run_step(step: Step, call: ToolCall, terminal: Next) -> ToolOutcome:
    async def go() -> ToolOutcome:
        return await step(call, CallCtx(), terminal)

    return asyncio.run(go())


def test_unknown_tool() -> None:
    terminal = FakeTerminal()
    outcome = run(chain(terminal), ToolCall(id="c1", name="device__nope", args={}))
    assert outcome.ok is False
    assert outcome.code == "unknown_tool"
    assert terminal.calls == []


@pytest.mark.parametrize("args", [{"level": 150}, {}, {"level": "30"}])
def test_invalid_args(args: dict[str, Any]) -> None:
    terminal = FakeTerminal()
    outcome = run(chain(terminal), ToolCall(id="c1", name="device__set_volume", args=args))
    assert outcome.ok is False
    assert outcome.code == "invalid_args"
    assert outcome.text.startswith("invalid arguments: ")
    assert terminal.calls == []


def test_valid_args_reach_terminal() -> None:
    terminal = FakeTerminal()
    call = ToolCall(id="c1", name="device__set_volume", args={"level": 30})
    outcome = run(chain(terminal), call)
    assert outcome.ok is True
    assert terminal.calls == [call]


def test_call_limit_across_rounds() -> None:
    terminal = FakeTerminal()
    next_ = chain(terminal)
    ctx = CallCtx()

    async def rounds() -> list[ToolOutcome]:
        outcomes: list[ToolOutcome] = []
        for group in range(2):
            calls = [
                ToolCall(id=f"c{group}{i}", name="device__set_volume", args={"level": 30})
                for i in range(3)
            ]
            outcomes += [await next_(c, ctx) for c in calls]
        return outcomes

    outcomes = asyncio.run(rounds())
    assert len(terminal.calls) == 4
    for outcome in outcomes[4:]:
        assert outcome.ok is False
        assert outcome.code == "call_limit"
        assert outcome.text == "tool call limit reached"


def test_result_is_untrusted_and_truncated() -> None:
    terminal = FakeTerminal(text="x" * 5000)
    step = wrap()
    call = ToolCall(id="c1", name="device__set_volume", args={"level": 30})
    outcome = run_step(step, call, terminal)
    assert outcome.text.startswith(UNTRUSTED_PREFIX)
    assert len(outcome.text) <= MAX_RESULT_CHARS


def test_insert_confirm_step() -> None:
    log: list[str] = []
    terminal = FakeTerminal()
    terminal.log = log

    async def confirm(call: ToolCall, ctx: CallCtx, next_: Next) -> ToolOutcome:
        log.append("confirm")
        return await next_(call, ctx)

    call = ToolCall(id="c1", name="device__set_volume", args={"level": 30})
    outcome = run(chain(terminal, [confirm]), call)
    assert outcome.ok is True
    assert log == ["confirm", "terminal"]


@pytest.mark.parametrize(
    ("schema", "args"),
    [
        ({"type": "object"}, None),
        ({"type": "object", "properties": {"x": {"type": "integer"}}}, {"x": True}),
        ({"type": "object", "properties": {"x": {"type": "integer", "minimum": 1}}}, {"x": 0}),
        ({"type": "object", "properties": {"x": {"enum": ["a", "b"]}}}, {"x": "c"}),
        ({"type": "object", "properties": {"x": {"type": "string"}}}, {"x": 1}),
        ({"type": "object", "additionalProperties": False}, {"y": 1}),
    ],
)
def test_validate_subset_rejects(schema: dict[str, Any], args: object) -> None:
    spec = ToolSpec(source="device", name="t", description="", schema=schema)
    terminal = FakeTerminal()
    step = validate({spec.full_name: spec})
    outcome = run_step(step, ToolCall(id="c1", name="device__t", args=args), terminal)
    assert outcome.code == "invalid_args"
    assert terminal.calls == []


def test_wrap_keeps_error_code() -> None:
    async def failing(call: ToolCall, ctx: CallCtx) -> ToolOutcome:
        return ToolOutcome(False, "boom", "device_error")

    call = ToolCall(id="c1", name="device__set_volume", args={"level": 30})
    outcome = run_step(wrap(), call, failing)
    assert outcome == ToolOutcome(False, UNTRUSTED_PREFIX + "boom", "device_error")


def test_validate_unknown_name_gives_unknown_tool() -> None:
    terminal = FakeTerminal()
    call = ToolCall(id="c1", name="device__nope", args={})
    outcome = run_step(validate(SPECS), call, terminal)
    assert outcome.code == "unknown_tool"
    assert terminal.calls == []


def test_validate_rejects_unknown_type_kind() -> None:
    spec = ToolSpec(source="device", name="t", description="", schema={"type": "number"})
    terminal = FakeTerminal()
    call = ToolCall(id="c1", name="device__t", args={})
    outcome = run_step(validate({spec.full_name: spec}), call, terminal)
    assert outcome.code == "invalid_args"
    assert terminal.calls == []

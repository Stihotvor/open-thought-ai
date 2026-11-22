"""The confirm chain step: a confirm=true call is held, never run (T5, D3)."""

import asyncio
import logging
from typing import Any

import pytest

import ota_server.hub.hub as hub_module
import ota_server.hub.steps as steps_module
from ota_server.hub.confirm import ConfirmGate, Held
from ota_server.hub.hub import McpHub
from ota_server.hub.types import CallCtx, Next, Step, ToolCall, ToolOutcome, ToolSpec

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"level": {"type": "integer"}},
    "additionalProperties": False,
}


class CountingSource:
    """A fake ToolSource that counts calls."""

    timeout = 1.0

    def __init__(self, name: str, tools: dict[str, bool]) -> None:
        self.name = name
        self._tools = tools
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(self.name, tool, "a tool", SCHEMA, confirm=flag)
            for tool, flag in self._tools.items()
        ]

    async def call(self, tool: str, args: dict[str, Any]) -> ToolOutcome:
        self.calls.append((tool, args))
        return ToolOutcome(True, "done")


def device_like() -> CountingSource:
    return CountingSource("device", {"set_volume": True, "set_face": False})


def mcp_like() -> CountingSource:
    return CountingSource("notes", {"add": True, "read": False})


def run(hub: McpHub, name: str, ctx: CallCtx, call_id: str = "c1") -> ToolOutcome:
    return asyncio.run(hub.run(ToolCall(call_id, name, {"level": 3}), ctx))


def held_call(gate: ConfirmGate, turn: int) -> Held | None:
    """Read the held call through the public phases."""
    if gate.arm(turn) is None or not gate.claim(turn):
        return None
    return gate.pop()


def test_confirm_false_call_passes() -> None:
    source = device_like()
    hub = McpHub([source])
    outcome = run(hub, "device__set_face", CallCtx(turn=1))
    assert outcome.ok is True
    assert source.calls == [("set_face", {"level": 3})]
    assert hub.gate.holding_turn() is None


@pytest.mark.parametrize("make, name", [(device_like, "set_volume"), (mcp_like, "add")])
def test_confirm_true_call_is_held(make: Any, name: str) -> None:
    source = make()
    hub = McpHub([source])
    full = f"{source.name}__{name}"
    outcome = run(hub, full, CallCtx(turn=5), call_id="c9")
    assert outcome.ok is False
    assert outcome.code == "confirm_required"
    assert "held: the user must say yes" in outcome.text
    assert source.calls == []
    held = held_call(hub.gate, 5)
    assert held is not None
    assert (held.name, held.args, held.call_id, held.turn) == (full, {"level": 3}, "c9", 5)


def test_second_confirm_call_is_busy() -> None:
    device, notes = device_like(), mcp_like()
    hub = McpHub([device, notes])
    ctx = CallCtx(turn=2)
    assert run(hub, "device__set_volume", ctx).code == "confirm_required"
    outcome = run(hub, "notes__add", ctx, call_id="c2")
    assert outcome.ok is False
    assert outcome.code == "confirm_busy"
    assert device.calls == [] and notes.calls == []
    held = held_call(hub.gate, 2)
    assert held is not None and held.name == "device__set_volume"


def test_normal_call_beside_held_call_runs() -> None:
    source = device_like()
    hub = McpHub([source])
    ctx = CallCtx(turn=2)
    run(hub, "device__set_volume", ctx)
    outcome = run(hub, "device__set_face", ctx, call_id="c2")
    assert outcome.ok is True
    assert source.calls == [("set_face", {"level": 3})]


class BrokenGate(ConfirmGate):
    def hold(self, held: Held) -> bool:
        raise RuntimeError("bug")


def test_step_bug_fails_closed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(hub_module, "ConfirmGate", BrokenGate)
    source = device_like()
    hub = McpHub([source])
    with caplog.at_level(logging.ERROR, logger="ota_server.hub.steps"):
        outcome = run(hub, "device__set_volume", CallCtx(turn=1))
    assert outcome.ok is False
    assert outcome.code == "confirm_required"
    assert source.calls == []
    errors = [r for r in caplog.records if r.name == "ota_server.hub.steps"]
    assert errors and errors[0].levelno == logging.ERROR
    assert errors[0].exc_info is not None


class OrderSource(CountingSource):
    def __init__(self, order: list[str]) -> None:
        super().__init__("device", {"set_face": False})
        self._order = order

    async def call(self, tool: str, args: dict[str, Any]) -> ToolOutcome:
        self._order.append("terminal")
        return await super().call(tool, args)


def test_chain_order(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []

    def recording(label: str, factory: Any) -> Any:
        def make(*args: Any) -> Step:
            inner = factory(*args)

            async def step(call: ToolCall, ctx: CallCtx, next_: Next) -> ToolOutcome:
                order.append(label)
                result: ToolOutcome = await inner(call, ctx, next_)
                return result

            return step

        return make

    for label in ("wrap", "resolve", "limit", "validate", "confirm"):
        monkeypatch.setattr(hub_module, label, recording(label, getattr(hub_module, label)))
    hub = McpHub([OrderSource(order)])
    assert run(hub, "device__set_face", CallCtx()).ok is True
    assert order == ["wrap", "resolve", "limit", "validate", "confirm", "terminal"]


def test_held_call_counts_toward_the_cap() -> None:
    source = device_like()
    hub = McpHub([source])
    ctx = CallCtx(calls=3, turn=4)
    assert run(hub, "device__set_volume", ctx).code == "confirm_required"
    outcome = run(hub, "device__set_face", ctx, call_id="c5")
    assert outcome.code == "call_limit"
    assert source.calls == []


def test_held_args_are_a_deep_copy() -> None:
    gate = ConfirmGate()
    step = steps_module.confirm({}, gate)
    tags: list[str] = ["a"]
    inner: list[int] = [1]
    call = ToolCall("c1", "notes__add", {"tags": tags, "meta": {"k": inner}})

    async def never(_call: ToolCall, _ctx: CallCtx) -> ToolOutcome:
        raise AssertionError("a held call never runs")

    outcome = asyncio.run(asyncio.wait_for(step(call, CallCtx(turn=1), never), 1))
    assert outcome.code == "confirm_required"
    tags.append("b")
    inner.append(2)
    held = held_call(gate, 1)
    assert held is not None
    assert held.args == {"tags": ["a"], "meta": {"k": [1]}}

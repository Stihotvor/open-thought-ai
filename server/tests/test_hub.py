"""Device source and McpHub: tool list, device calls, timeout, limits and frame size (T4)."""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from ota_server.hub.device_source import FACES, DeviceSource
from ota_server.hub.hub import McpHub
from ota_server.hub.pending import DeviceResult, PendingCalls, PendingClosed
from ota_server.hub.steps import UNTRUSTED_PREFIX
from ota_server.hub.types import MAX_RESULT_CHARS, CallCtx, ToolCall, ToolOutcome, ToolSpec
from ota_server.protocol import MAX_TEXT_FRAME


class FakeSend:
    def __init__(self, error: Exception | None = None) -> None:
        self.frames: list[dict[str, Any]] = []
        self.error = error

    async def __call__(self, frame: dict[str, Any]) -> None:
        if self.error is not None:
            raise self.error
        self.frames.append(frame)


OK = DeviceResult(True, "ok")


class FakePending(PendingCalls):
    """Runs send, then answers with `result` or raises `error`."""

    def __init__(self, result: DeviceResult = OK, error: Exception | None = None) -> None:
        super().__init__()
        self.result = result
        self.error = error

    async def wait_for(
        self, call_id: int, timeout: float, send: Callable[[], Awaitable[None]]
    ) -> DeviceResult:
        await send()
        if self.error is not None:
            raise self.error
        return self.result


def make_hub(
    pending: PendingCalls | None = None, send: FakeSend | None = None
) -> tuple[McpHub, FakeSend]:
    send = send or FakeSend()
    source = DeviceSource(send, pending if pending is not None else FakePending())
    return McpHub([source]), send


def run(hub: McpHub, name: str, args: object) -> ToolOutcome:
    return asyncio.run(hub.run(ToolCall("c1", name, args), CallCtx()))


def test_tool_list() -> None:
    hub, _ = make_hub()
    tools = hub.list_tools()
    names = [t["function"]["name"] for t in tools]
    assert names == ["device__set_volume", "device__set_face", "device__get_status"]
    assert all(t["type"] == "function" for t in tools)
    face = tools[1]["function"]["parameters"]["properties"]["face"]
    assert face["enum"] == list(FACES)
    assert len(face["enum"]) == 6


def test_volume_call() -> None:
    hub, send = make_hub()
    outcome = run(hub, "device__set_volume", {"level": 30})
    assert outcome.ok is True
    assert len(send.frames) == 1
    frame = send.frames[0]
    assert isinstance(frame["id"], int)
    assert frame == {
        "type": "tool.call",
        "id": frame["id"],
        "name": "set_volume",
        "args": {"level": 30},
    }


def test_timeout() -> None:
    hub, send = make_hub(PendingCalls())
    start = time.monotonic()
    outcome = run(hub, "device__get_status", {})
    elapsed = time.monotonic() - start
    assert outcome.ok is False
    assert outcome.code == "tool_timeout"
    assert "the device did not answer in time" in outcome.text
    assert 1.8 <= elapsed <= 2.2
    assert len(send.frames) == 1


def test_device_error() -> None:
    hub, _ = make_hub(FakePending(DeviceResult(False, "no speaker")))
    outcome = run(hub, "device__set_volume", {"level": 30})
    assert outcome.ok is False
    assert outcome.code == "device_error"
    assert "no speaker" in outcome.text


def test_six_calls_in_one_turn() -> None:
    hub, send = make_hub()
    tools = hub.turn_context()

    async def scenario() -> list[ToolOutcome]:
        return [await tools.run(ToolCall(f"c{i}", "device__get_status", {})) for i in range(6)]

    outcomes = asyncio.run(scenario())
    assert len(send.frames) == 4
    assert all(o.ok for o in outcomes[:4])
    assert [o.code for o in outcomes[4:]] == ["call_limit", "call_limit"]
    assert tools.list_tools() == hub.list_tools()


def test_unknown_tool_and_invalid_args_send_nothing() -> None:
    hub, send = make_hub()
    calls: list[tuple[str, object]] = [
        ("device__nope", {}),
        ("device__set_volume", {"level": 150}),
        ("device__set_volume", {}),
        ("device__set_volume", {"level": "x"}),
    ]
    outcomes = [run(hub, name, args) for name, args in calls]
    assert all(o.ok is False for o in outcomes)
    assert send.frames == []


def test_oversized_frame() -> None:
    send = FakeSend()
    source = DeviceSource(send, FakePending())
    outcome = asyncio.run(source.call("set_face", {"face": "x" * MAX_TEXT_FRAME}))
    assert outcome.ok is False
    assert outcome.code == "frame_too_large"
    assert send.frames == []


def test_frames_fit() -> None:
    hub, send = make_hub()
    run(hub, "device__set_volume", {"level": 100})
    for face in FACES:
        run(hub, "device__set_face", {"face": face})
    run(hub, "device__get_status", {})
    assert len(send.frames) == 2 + len(FACES)
    for frame in send.frames:
        assert len(json.dumps(frame, separators=(",", ":")).encode()) <= MAX_TEXT_FRAME


def test_device_closed() -> None:
    hub, _ = make_hub(FakePending(error=PendingClosed()))
    outcome = run(hub, "device__get_status", {})
    assert outcome.ok is False
    assert outcome.code == "device_closed"


def test_send_failed() -> None:
    hub, _ = make_hub(PendingCalls(), FakeSend(OSError("broken pipe")))
    outcome = run(hub, "device__get_status", {})
    assert outcome.ok is False
    assert outcome.code == "send_failed"


def test_run_never_raises_on_source_bug() -> None:
    class Broken(DeviceSource):
        async def call(self, tool: str, args: dict[str, Any]) -> ToolOutcome:
            raise RuntimeError("bug")

    hub = McpHub([Broken(FakeSend(), FakePending())])
    outcome = run(hub, "device__get_status", {})
    assert outcome.ok is False
    assert outcome.code == "internal"


def test_one_info_line_per_call_without_args_or_results(caplog: pytest.LogCaptureFixture) -> None:
    hub, _ = make_hub(FakePending(DeviceResult(True, "secret-result")))
    with caplog.at_level(logging.INFO, logger="ota_server.hub.hub"):
        run(hub, "device__set_volume", {"level": 37})
        run(hub, "device__nope", {})
    lines = [r.getMessage() for r in caplog.records if r.name == "ota_server.hub.hub"]
    assert len(lines) == 2
    assert "device__set_volume" in lines[0] and "True" in lines[0]
    assert "device__nope" in lines[1] and "unknown_tool" in lines[1]
    assert not any("37" in line or "secret-result" in line for line in lines)


def test_invalid_args_outcome_is_wrapped_and_capped() -> None:
    hub, send = make_hub()
    outcome = run(hub, "device__set_volume", {"level": 30, "k" * 4000: 1})
    assert outcome.code == "invalid_args"
    assert outcome.text.startswith(UNTRUSTED_PREFIX + "invalid arguments: ")
    assert len(outcome.text) <= MAX_RESULT_CHARS
    assert send.frames == []


def test_call_limit_outcome_is_wrapped() -> None:
    hub, _ = make_hub()
    tools = hub.turn_context()

    async def scenario() -> list[ToolOutcome]:
        return [await tools.run(ToolCall(f"c{i}", "device__get_status", {})) for i in range(5)]

    assert asyncio.run(scenario())[-1].text == UNTRUSTED_PREFIX + "tool call limit reached"


class SlowSource:
    """A source that ignores its own timeout: only the hub's outer guard ends it."""

    name = "slow"
    timeout = 0.05

    def specs(self) -> list[ToolSpec]:
        return [ToolSpec("slow", "wait", "Wait.", {"type": "object"})]

    async def call(self, tool: str, args: dict[str, Any]) -> ToolOutcome:
        await asyncio.sleep(10)
        return ToolOutcome(True, "late")


def test_outer_guard_ends_a_slow_source() -> None:
    hub = McpHub([SlowSource()])
    start = time.monotonic()
    outcome = run(hub, "slow__wait", {})
    assert time.monotonic() - start < 1.0
    assert outcome.ok is False
    assert outcome.code == "tool_timeout"
    assert outcome.text.startswith(UNTRUSTED_PREFIX)


def named(source: str, tool: str) -> SlowSource:
    fake = SlowSource()
    fake.name = source
    fake.specs = lambda: [ToolSpec(source, tool, "", {"type": "object"})]  # type: ignore[method-assign]
    return fake


@pytest.mark.parametrize(
    ("source", "tool"),
    [("dev__ice", "x"), ("device", "bad name"), ("device", "x" * 60), ("device", "a.b")],
)
def test_bad_source_or_tool_name_is_rejected(source: str, tool: str) -> None:
    with pytest.raises(ValueError):
        McpHub([named(source, tool)])


def test_good_names_are_accepted() -> None:
    hub = McpHub([named("my-src_1", "tool-name_2")])
    assert [t["function"]["name"] for t in hub.list_tools()] == ["my-src_1__tool-name_2"]


def test_device_timeout_fires_before_the_outer_guard(caplog: pytest.LogCaptureFixture) -> None:
    hub = McpHub([DeviceSource(FakeSend(), PendingCalls(), timeout=0.05)])
    with caplog.at_level(logging.WARNING):
        outcome = run(hub, "device__get_status", {})
    assert outcome.code == "tool_timeout"
    names = [r.name for r in caplog.records if "timed out" in r.getMessage()]
    assert names == ["ota_server.hub.device_source"]

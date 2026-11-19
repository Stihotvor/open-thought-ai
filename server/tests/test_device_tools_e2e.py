"""Device tools end to end (T6): DeviceSession + VoicePipeline + McpHub + DeviceSource.
A fake chat endpoint asks for tools; a fake socket plays the device. No board, no real API."""

import asyncio
import json
import time
from collections.abc import AsyncGenerator, Callable
from typing import Any

import httpx
import pytest
from fakes import SPEECH, Endpoint, ListSocket, pipeline_for, sse, tts_wav

from ota_server.hub.types import ToolCall, ToolContext, ToolOutcome
from ota_server.protocol import MAX_CREDIT
from ota_server.reply import History, Notify, ReplyChunk, no_notify
from ota_server.session import DeviceSession, echo

VOLUME = ("device__set_volume", '{"level": 30}')
TOOL = "tool"  # a chat script step: answer with tool_calls
Answer = Callable[[int], list[dict[str, Any]]]  # call id -> the tool.result frames to send


def tool_sse(calls: list[tuple[str, str]]) -> str:
    lines = []
    for index, (name, arguments) in enumerate(calls):
        call = {
            "index": index,
            "id": f"call_{index}",
            "function": {"name": name, "arguments": arguments},
        }
        lines.append("data: " + json.dumps({"choices": [{"delta": {"tool_calls": [call]}}]}))
    return "\n\n".join([*lines, "data: [DONE]"]) + "\n\n"


class ToolEndpoint(Endpoint):
    """The fake endpoint whose chat follows a script: TOOL asks for `calls`, else text."""

    def __init__(self, script: list[str], calls: list[tuple[str, str]] | None = None) -> None:
        super().__init__(reply="Głośność ustawiona.", tts=tts_wav(0.05))
        self.script, self.tool_calls = script, calls or [VOLUME]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if not req.url.path.endswith("/chat/completions"):
            return super().__call__(req)
        self.calls.append("completions")
        self.chats.append(json.loads(req.content)["messages"])
        if self.script[len(self.chats) - 1] == TOOL:
            return httpx.Response(200, text=tool_sse(self.tool_calls))
        return httpx.Response(200, text=sse(self.reply))


def ok(call_id: int) -> list[dict[str, Any]]:
    return [{"type": "tool.result", "id": call_id, "ok": True, "result": "volume 30"}]


def never(_call_id: int) -> list[dict[str, Any]]:
    return []


class DeviceSocket(ListSocket):
    """The fake device: records frames and answers each tool.call with `answer(id)`."""

    def __init__(self, answer: Answer = never) -> None:
        super().__init__()
        self.answer = answer
        self.session: DeviceSession | None = None
        self.tasks: list[asyncio.Task[None]] = []

    async def send_json(self, msg: dict[str, Any]) -> None:
        await super().send_json(msg)
        if msg["type"] == "tool.call":
            self.tasks.append(asyncio.create_task(self.reply_to(msg["id"])))

    async def reply_to(self, call_id: int) -> None:
        assert self.session is not None
        for frame in self.answer(call_id):
            await self.session.handle_text(frame)


class Recorder:
    """A ToolContext around the turn's real one: keeps each outcome and its wait."""

    def __init__(self, inner: ToolContext, log: list[tuple[ToolOutcome, float]]) -> None:
        self.inner, self.log = inner, log

    def list_tools(self) -> list[dict[str, Any]]:
        return self.inner.list_tools()

    async def run(self, call: ToolCall) -> ToolOutcome:
        started = time.monotonic()
        outcome = await self.inner.run(call)
        self.log.append((outcome, time.monotonic() - started))
        return outcome


def new_session(endpoint: Endpoint, socket: DeviceSocket) -> DeviceSession:
    session = DeviceSession(socket, pipeline_for(endpoint))  # type: ignore[arg-type]
    socket.session = session
    return session


async def start_turn(session: DeviceSession) -> None:
    await session.handle_text({"type": "listen.start"})
    session.credit = MAX_CREDIT
    session.audio.extend(SPEECH)
    await session.handle_text({"type": "listen.stop", "turn": session.turn})


async def run_turn(session: DeviceSession) -> None:
    await start_turn(session)
    assert session.reply_task is not None
    await session.reply_task


def tool_calls(sent: list[Any]) -> list[dict[str, Any]]:
    return [m for m in sent if isinstance(m, dict) and m["type"] == "tool.call"]


def ends(sent: list[Any]) -> list[dict[str, Any]]:
    return [m for m in sent if isinstance(m, dict) and m["type"] == "reply.end"]


def tool_messages(chat: list[dict[str, Any]]) -> list[str]:
    return [m["content"] for m in chat if m["role"] == "tool"]


async def wait_for_calls(socket: DeviceSocket, count: int) -> None:
    async def poll() -> None:
        while len(tool_calls(socket)) < count:
            await asyncio.sleep(0.005)

    await asyncio.wait_for(poll(), 1.0)


def test_volume_turn_end_to_end() -> None:
    endpoint, socket = ToolEndpoint([TOOL, "text"]), DeviceSocket(ok)
    asyncio.run(run_turn(new_session(endpoint, socket)))
    calls = tool_calls(socket)
    assert len(calls) == 1
    assert {k: v for k, v in calls[0].items() if k != "id"} == {
        "type": "tool.call",
        "name": "set_volume",
        "args": {"level": 30},
    }
    assert isinstance(calls[0]["id"], int) and not isinstance(calls[0]["id"], bool)
    assert tool_messages(endpoint.chats[1])[0].endswith("volume 30")
    start = socket.index({"type": "reply.start", "turn": 1})
    assert any(isinstance(m, bytes) for m in socket[start:])
    assert ends(socket) == [{"type": "reply.end", "turn": 1, "reason": "done"}]


def test_no_tool_result_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint, socket = ToolEndpoint([TOOL, "text"]), DeviceSocket(never)
    session = new_session(endpoint, socket)
    outcomes: list[tuple[ToolOutcome, float]] = []
    real = session.turn_tools
    monkeypatch.setattr(session, "turn_tools", lambda: Recorder(real(), outcomes))
    asyncio.run(run_turn(session))
    [(outcome, waited)] = outcomes
    assert outcome.code == "tool_timeout"
    assert 1.8 <= waited <= 2.2
    assert "the device did not answer in time" in tool_messages(endpoint.chats[1])[0]
    assert ends(socket) == [{"type": "reply.end", "turn": 1, "reason": "done"}]


def test_abort_during_the_wait() -> None:
    async def scenario() -> DeviceSession:
        session = new_session(ToolEndpoint([TOOL, "text"]), DeviceSocket(never))
        await start_turn(session)
        await wait_for_calls(session.ws, 1)  # type: ignore[arg-type]
        assert len(session.pending) == 1
        await session.handle_text({"type": "abort", "turn": session.turn})
        return session

    session = asyncio.run(scenario())
    assert len(session.pending) == 0
    assert session.reply_task is None
    assert ends(session.ws) == [{"type": "reply.end", "turn": 1, "reason": "abort"}]  # type: ignore[arg-type]


def test_late_result_after_abort_is_ignored() -> None:
    late: dict[str, int] = {}

    def answer(call_id: int) -> list[dict[str, Any]]:
        if "id" not in late:
            return []  # the first turn's call: the device never answers
        stale = {"type": "tool.result", "id": late["id"], "ok": False, "error": "late"}
        return [stale, *ok(call_id)]

    async def scenario() -> tuple[ToolEndpoint, DeviceSocket]:
        endpoint, socket = ToolEndpoint([TOOL, TOOL, "text"]), DeviceSocket(answer)
        session = new_session(endpoint, socket)
        await start_turn(session)
        await wait_for_calls(socket, 1)
        late["id"] = tool_calls(socket)[0]["id"]
        await session.handle_text({"type": "abort", "turn": session.turn})
        await run_turn(session)
        return endpoint, socket

    endpoint, socket = asyncio.run(scenario())
    first, second = tool_calls(socket)
    assert second["id"] != first["id"]
    [content] = tool_messages(endpoint.chats[2])
    assert content.endswith("volume 30") and "late" not in content
    assert ends(socket)[-1] == {"type": "reply.end", "turn": 2, "reason": "done"}


def test_echo_gets_tools_and_ignores_them() -> None:
    seen: list[object] = []

    async def spy(
        pcm: bytes, history: History, *, notify: Notify = no_notify, tools: Any = None
    ) -> AsyncGenerator[ReplyChunk]:
        seen.append(tools)
        async for chunk in echo(pcm, history, notify=notify, tools=tools):
            yield chunk

    socket = DeviceSocket()
    session = DeviceSession(socket, spy)  # type: ignore[arg-type]
    asyncio.run(run_turn(session))
    assert len(seen) == 1 and hasattr(seen[0], "list_tools")
    audio = b"".join(m[1:] for m in socket if isinstance(m, bytes))
    assert audio == SPEECH
    assert not tool_calls(socket)
    assert ends(socket) == [{"type": "reply.end", "turn": 1, "reason": "done"}]


def test_cap_per_turn() -> None:
    endpoint = ToolEndpoint([TOOL, "text"], [VOLUME] * 6)
    socket = DeviceSocket(ok)
    asyncio.run(run_turn(new_session(endpoint, socket)))
    assert len(tool_calls(socket)) == 4
    results = tool_messages(endpoint.chats[1])
    assert len(results) == 6 and all("limit" in text for text in results[4:])

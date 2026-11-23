"""DeviceSession parts that the gateway tests cannot time: pacing, credit and cancel."""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any, cast

import pytest

from ota_server import session as session_module
from ota_server.hub.device_source import DeviceSource
from ota_server.hub.hub import McpHub
from ota_server.hub.pending import DeviceResult, PendingCalls, PendingClosed
from ota_server.hub.types import ToolCall, ToolContext, ToolOutcome, ToolSpec
from ota_server.protocol import AUDIO_CHUNK, MAX_CREDIT, MAX_LEAD_SECONDS, TRANSCRIPT_MAX_CHARS
from ota_server.reply import History, Notify, ReplyChunk, ReplySource, no_notify
from ota_server.session import CreditStalled, DeviceSession, echo


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    async def send_json(self, msg: dict[str, Any]) -> None:
        self.sent.append(msg)

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)


def new_session() -> DeviceSession:
    return DeviceSession(FakeSocket())  # type: ignore[arg-type]


def fake_ws(session: DeviceSession) -> FakeSocket:
    return cast(FakeSocket, session.ws)


def test_late_audio_is_paced_from_now() -> None:
    async def scenario() -> tuple[float, float, float]:
        session = new_session()
        session.credit = MAX_CREDIT * 10
        late_clock = time.monotonic() - 5.0  # the device ran dry 5 s ago
        started = time.monotonic()
        clock = await session.stream(1, b"\0" * AUDIO_CHUNK * 20, 0, late_clock)
        return clock, late_clock, time.monotonic() - started

    clock, late_clock, took = asyncio.run(scenario())
    assert clock > late_clock + 4.5  # paced again, not sent as one 5 s burst
    # 2 s of audio, at most MAX_LEAD_SECONDS ahead: the rest waits for real time
    assert took >= 2.0 - MAX_LEAD_SECONDS - 0.15


def test_credit_is_capped_at_the_device_buffer() -> None:
    session = new_session()
    asyncio.run(session.handle_text({"type": "audio.credit", "bytes": 10**9}))
    assert session.credit == MAX_CREDIT


def test_no_credit_ends_the_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_module, "CREDIT_TIMEOUT", 0.05)
    session = new_session()
    with pytest.raises(CreditStalled):
        asyncio.run(session.stream(1, b"\0" * AUDIO_CHUNK, 0, time.monotonic()))


def test_cancel_logs_a_crashed_reply_task(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        session = new_session()

        async def crash() -> None:
            raise ConnectionError("socket closed")

        session.reply_task = asyncio.create_task(crash())
        await asyncio.sleep(0)
        await session.cancel_reply()  # must not raise into the next listen.start

    with caplog.at_level(logging.ERROR):
        asyncio.run(scenario())
    assert "reply task failed" in caplog.text


def test_abort_during_the_last_message_adds_no_second_end() -> None:
    async def scenario() -> list[Any]:
        session = new_session()
        session.open_turn = 1
        gate = asyncio.Event()
        sent = fake_ws(session).sent

        async def slow_send(msg: dict[str, Any]) -> None:
            sent.append(msg)  # on the wire, but the send has not returned yet
            await gate.wait()

        fake_ws(session).send_json = slow_send  # type: ignore[method-assign]
        session.reply_task = asyncio.create_task(
            session.finish({"type": "reply.end", "turn": 1, "reason": "done"})
        )
        await asyncio.sleep(0)  # the last message is being sent
        await session.cancel_reply()
        return sent

    ends = [m for m in asyncio.run(scenario()) if m.get("type") == "reply.end"]
    assert ends == [{"type": "reply.end", "turn": 1, "reason": "done"}]


def spoken_reply(texts: list[str], **session_args: Any) -> DeviceSession:
    """Run one reply of 100 ms chunks through a session with plenty of credit."""

    async def source(
        pcm: bytes, history: History, *, notify: Notify = no_notify, tools: Any = None
    ) -> AsyncGenerator[ReplyChunk]:
        for text in texts:
            yield ReplyChunk(b"\0" * AUDIO_CHUNK, text, "question")

    async def scenario() -> DeviceSession:
        session = DeviceSession(FakeSocket(), source, **session_args)  # type: ignore[arg-type]
        session.credit = MAX_CREDIT
        session.open_turn = 1
        await session.reply(1, b"\0" * AUDIO_CHUNK * 4)
        return session

    return asyncio.run(scenario())


def test_a_played_reply_is_remembered() -> None:
    session = spoken_reply(["One.", "Two."])
    assert list(session.history) == [("question", "One. Two.")]


def test_a_chunk_cut_by_the_cap_is_marked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_module, "SPEECH_CAP", AUDIO_CHUNK + 1)
    session = spoken_reply(["One.", "Two.", "Three."])
    assert list(session.history) == [("question", "One. Two. …")]


def test_a_cut_first_chunk_keeps_the_question(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_module, "SPEECH_CAP", AUDIO_CHUNK // 2)
    session = spoken_reply(["A long story."])
    assert list(session.history) == [("question", "A long story. …")]


def test_history_turns_is_a_sliding_window() -> None:
    session = spoken_reply(["a."], history_turns=2)
    session.remember([ReplyChunk(b"", "b.", "q2")])
    session.remember([ReplyChunk(b"", "c.", "q3")])
    assert list(session.history) == [("q2", "b."), ("q3", "c.")]


def test_an_aborted_reply_keeps_only_the_played_chunks() -> None:
    async def scenario() -> DeviceSession:
        async def source(
            pcm: bytes, history: History, *, notify: Notify = no_notify, tools: Any = None
        ) -> AsyncGenerator[ReplyChunk]:
            yield ReplyChunk(b"\0" * AUDIO_CHUNK, "One.", "question")
            yield ReplyChunk(b"\0" * AUDIO_CHUNK * 50, "Long.", "question")

        session = DeviceSession(FakeSocket(), source)  # type: ignore[arg-type]
        session.credit = MAX_CREDIT
        session.open_turn = 1
        session.reply_task = asyncio.create_task(session.reply(1, b"\0" * AUDIO_CHUNK * 4))
        while sum(isinstance(m, bytes) for m in fake_ws(session).sent) < 2:
            await asyncio.sleep(0.01)  # wait until "Long." is playing
        await session.cancel_reply()
        return session

    assert list(asyncio.run(scenario()).history) == [("question", "One. Long. …")]


def test_echo_turns_leave_no_history() -> None:
    assert not spoken_reply([""]).history


def reply_messages(source: ReplySource = echo) -> list[dict[str, Any]]:
    """Run one reply with plenty of credit: the text messages the device gets."""

    async def scenario() -> list[dict[str, Any]]:
        socket = FakeSocket()
        session = DeviceSession(socket, source)  # type: ignore[arg-type]
        session.credit = MAX_CREDIT
        session.open_turn = 1
        await session.reply(1, b"\0" * AUDIO_CHUNK * 4)
        return [m for m in socket.sent if isinstance(m, dict)]

    return asyncio.run(scenario())


def transcript_turn(text: str) -> list[dict[str, Any]]:
    """Run one reply whose source calls notify(text), then yields reply audio."""

    async def source(
        pcm: bytes, history: History, *, notify: Notify = no_notify, tools: Any = None
    ) -> AsyncGenerator[ReplyChunk]:
        await notify(text)
        yield ReplyChunk(b"\0" * AUDIO_CHUNK, "Hi.", text)

    return reply_messages(source)


def test_transcript_is_sent_before_the_reply() -> None:
    msgs = transcript_turn("hello")
    assert {"type": "transcript", "turn": 1, "text": "hello"} in msgs
    types = [m["type"] for m in msgs]
    assert types.index("transcript") < types.index("reply.start")


def test_transcript_text_is_truncated() -> None:
    msgs = transcript_turn("a" * 300)
    assert [m["text"] for m in msgs if m["type"] == "transcript"] == ["a" * TRANSCRIPT_MAX_CHARS]


def test_no_transcript_after_abort() -> None:
    async def scenario() -> list[Any]:
        notifiers: list[Notify] = []
        waiting = asyncio.Event()

        async def source(
            pcm: bytes, history: History, *, notify: Notify = no_notify, tools: Any = None
        ) -> AsyncGenerator[ReplyChunk]:
            notifiers.append(notify)
            waiting.set()
            await asyncio.Event().wait()  # thinking until the abort
            yield ReplyChunk(pcm)

        socket = FakeSocket()
        session = DeviceSession(socket, source)  # type: ignore[arg-type]
        session.turn = session.open_turn = 1
        session.reply_task = asyncio.create_task(session.reply(1, b"\0" * AUDIO_CHUNK * 4))
        await waiting.wait()
        await session.handle_text({"type": "abort", "turn": 1})
        await notifiers[0]("x")  # a late call from the aborted turn
        return socket.sent

    sent = asyncio.run(scenario())
    assert sent[-1] == {"type": "reply.end", "turn": 1, "reason": "abort"}
    assert not [m for m in sent if m.get("type") == "transcript"]


def test_echo_sends_no_transcript() -> None:
    msgs = reply_messages()
    assert msgs[-1] == {"type": "reply.end", "turn": 1, "reason": "done"}
    assert "transcript" not in [m["type"] for m in msgs]


def test_echo_ignores_tools() -> None:
    async def chunks(**kwargs: Any) -> list[ReplyChunk]:
        return [chunk async for chunk in echo(b"pcm", (), **kwargs)]

    tools = cast(ToolContext, object())
    assert asyncio.run(chunks()) == asyncio.run(chunks(tools=tools)) == [ReplyChunk(b"pcm")]


async def no_send() -> None:
    pass


async def wait_until(check: Callable[[], bool]) -> None:
    while not check():
        await asyncio.sleep(0.001)


async def endless(
    pcm: bytes, history: History, *, notify: Notify = no_notify, tools: Any = None
) -> AsyncGenerator[ReplyChunk]:
    while True:
        yield ReplyChunk(b"\0" * AUDIO_CHUNK)


async def enter_state(session: DeviceSession, state: str) -> None:
    """Put the session in the idle, listening or speaking state."""
    if state in ("listening", "speaking"):
        await session.handle_text({"type": "listen.start"})
    if state == "speaking":
        session.credit = MAX_CREDIT
        session.reply_source = endless
        session.audio.extend(b"\0" * AUDIO_CHUNK * 4)
        await session.handle_text({"type": "listen.stop", "turn": session.turn})
        sent = fake_ws(session).sent
        await wait_until(lambda: {"type": "reply.start", "turn": session.turn} in sent)


@pytest.mark.parametrize("state", ["idle", "listening", "speaking"])
def test_tool_result_resolves_the_wait_in_every_state(state: str) -> None:
    async def scenario() -> tuple[DeviceResult, int]:
        session = new_session()
        await enter_state(session, state)
        waiter = asyncio.create_task(session.pending.wait_for(1, 1.0, no_send))
        await wait_until(lambda: len(session.pending) == 1)
        await session.handle_text({"type": "tool.result", "id": 1, "ok": True, "result": "30"})
        result = await waiter
        await session.cancel_reply()
        return result, len(session.pending)

    assert asyncio.run(scenario()) == (DeviceResult(True, "30"), 0)


def test_unknown_and_answered_ids_are_ignored(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> list[Any]:
        session = new_session()
        waiter = asyncio.create_task(session.pending.wait_for(1, 1.0, no_send))
        await wait_until(lambda: len(session.pending) == 1)
        await session.handle_text({"type": "tool.result", "id": 1, "ok": True})
        await waiter
        await session.handle_text({"type": "tool.result", "id": 99, "ok": True})
        await session.handle_text({"type": "tool.result", "id": 1, "ok": True})
        await session.handle_text({"type": "tool.result", "id": 7})  # malformed and unknown
        await session.handle_text({"type": "listen.start"})
        session.credit = MAX_CREDIT
        session.audio.extend(b"\0" * AUDIO_CHUNK * 4)
        await session.handle_text({"type": "listen.stop", "turn": session.turn})
        assert session.reply_task is not None
        await session.reply_task
        return fake_ws(session).sent

    with caplog.at_level(logging.INFO):
        sent = asyncio.run(scenario())
    assert caplog.text.count("tool.result") >= 3
    assert sent[-1] == {"type": "reply.end", "turn": 1, "reason": "done"}


def test_abort_cancels_the_wait_and_leaves_no_entry() -> None:
    async def scenario() -> tuple[bool, int]:
        session = new_session()

        async def tool_source(
            pcm: bytes, history: History, *, notify: Notify = no_notify, tools: Any = None
        ) -> AsyncGenerator[ReplyChunk]:
            await session.pending.wait_for(session.pending.new_id(), 30.0, no_send)
            yield ReplyChunk(pcm)

        session.reply_source = tool_source
        session.turn = session.open_turn = 1
        session.reply_task = asyncio.create_task(session.reply(1, b"\0" * AUDIO_CHUNK * 4))
        await wait_until(lambda: len(session.pending) == 1)
        task = session.reply_task
        await session.handle_text({"type": "abort", "turn": 1})
        return task.cancelled(), len(session.pending)

    assert asyncio.run(scenario()) == (True, 0)


def test_close_fails_every_wait_and_leaves_no_task() -> None:
    async def scenario() -> tuple[list[Any], int]:
        session = new_session()
        waits = [asyncio.create_task(session.pending.wait_for(i, 30.0, no_send)) for i in (1, 2)]
        await wait_until(lambda: len(session.pending) == 2)
        await session.close()
        results = await asyncio.gather(*waits, return_exceptions=True)
        return results, len(asyncio.all_tasks()) - 1  # all but this task

    results, left = asyncio.run(scenario())
    assert all(isinstance(r, PendingClosed) for r in results)
    assert left == 0


def test_tool_result_after_cancel_keeps_the_session() -> None:
    async def scenario() -> list[Any]:
        session = new_session()
        waiter = asyncio.create_task(session.pending.wait_for(1, 5.0, no_send))
        await wait_until(lambda: len(session.pending) == 1)
        waiter.cancel()
        await session.handle_text({"type": "tool.result", "id": 1, "ok": True})
        with pytest.raises(asyncio.CancelledError):
            await waiter
        await session.handle_text({"type": "listen.start"})
        return fake_ws(session).sent

    assert asyncio.run(scenario())[-1] == {"type": "turn.start", "turn": 1}


def test_two_turns_share_one_hub_with_their_own_cap() -> None:
    async def scenario() -> list[list[str | None]]:
        session = new_session()
        session.pending = FakePendingOk()
        session.hub = McpHub([DeviceSource(session.ws.send_json, session.pending)])
        hub = session.hub
        codes: list[list[str | None]] = []
        for _ in range(2):
            tools = session.turn_tools()
            assert session.hub is hub
            call = ToolCall("c", "device__get_status", {})
            codes.append([(await tools.run(call)).code for _ in range(5)])
        return codes

    first, second = asyncio.run(scenario())
    assert first == second == [None, None, None, None, "call_limit"]


def test_the_hub_is_built_once_per_session() -> None:
    session = new_session()
    assert isinstance(session.hub, McpHub)
    hub = session.hub
    session.turn_tools()
    session.turn_tools()
    assert session.hub is hub


class OneToolSource:
    """A ToolSource with the one tool "t"."""

    name = "ext"
    timeout = 1.0

    def specs(self) -> list[ToolSpec]:
        return [ToolSpec(self.name, "t", "A test tool.", {"type": "object"}, confirm=False)]

    async def call(self, tool: str, args: dict[str, Any]) -> ToolOutcome:
        return ToolOutcome(True, tool)


def hub_names(session: DeviceSession) -> list[str]:
    return [tool["function"]["name"] for tool in session.hub.list_tools()]


def test_the_hub_holds_the_given_source_tools() -> None:
    session = DeviceSession(FakeSocket(), mcp_sources=[OneToolSource()])  # type: ignore[arg-type]
    names = hub_names(session)
    assert "ext__t" in names
    assert any(name.startswith("device__") for name in names)


def test_the_default_hub_lists_only_device_tools() -> None:
    names = hub_names(new_session())
    assert names and all(name.startswith("device__") for name in names)


class FakePendingOk(PendingCalls):
    async def wait_for(
        self, call_id: int, timeout: float, send: Callable[[], Awaitable[None]]
    ) -> DeviceResult:
        await send()
        return DeviceResult(True, "ok")

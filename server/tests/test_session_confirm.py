"""Session side of the confirm flow: arm, the 10 s timer, listen.start, abort, close (T8, D9)."""

import asyncio
from collections.abc import AsyncGenerator
from typing import Any, cast

import pytest

from ota_server import session as session_module
from ota_server.hub.types import ToolCall, ToolContext, ToolOutcome, ToolSpec
from ota_server.protocol import AUDIO_CHUNK, MIN_SPEECH, TurnError
from ota_server.ratelimit import TokenBucket
from ota_server.reply import History, Notify, ReplyChunk, no_notify
from ota_server.session import DeviceSession

SCHEMA: dict[str, Any] = {"type": "object", "properties": {"level": {"type": "integer"}}}
TOOL = "lamp__set_volume"
SPEECH = b"\0" * MIN_SPEECH


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    async def send_json(self, msg: dict[str, Any]) -> None:
        self.sent.append(msg)

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)

    def json(self) -> list[dict[str, Any]]:
        return [m for m in self.sent if isinstance(m, dict)]

    def codes(self) -> list[str]:
        return [m.get("code", "") for m in self.json() if m["type"] == "error"]

    def types(self) -> list[str]:
        return [m["type"] for m in self.json()]


class FakeSleep:
    """Records the delay and waits until the test releases it."""

    def __init__(self) -> None:
        self.delays: list[float] = []
        self.released = asyncio.Event()

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)
        await self.released.wait()


class Lamp:
    """A ToolSource with one confirm=true tool that counts its runs."""

    name = "lamp"
    timeout = 1.0

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def specs(self) -> list[ToolSpec]:
        return [ToolSpec(self.name, "set_volume", "a tool", SCHEMA, confirm=True)]

    async def call(self, tool: str, args: dict[str, Any]) -> ToolOutcome:
        self.calls.append(args)
        return ToolOutcome(True, "ok")


class Script:
    """A reply source. Each turn runs the next mode:
    hold: holds a call, then one short chunk; cap: holds, then a chunk over the cap;
    moderation: holds, one chunk, then a TurnError; question: holds, one chunk, then waits;
    yes: answers the held call with "yes"; plain: one chunk."""

    def __init__(self, *modes: str) -> None:
        self.modes = list(modes)
        self.outcomes: list[ToolOutcome] = []
        self.answers: list[str | None] = []
        self.forever = asyncio.Event()

    async def __call__(
        self,
        pcm: bytes,
        _history: History,
        *,
        notify: Notify = no_notify,
        tools: ToolContext | None = None,
    ) -> AsyncGenerator[ReplyChunk]:
        assert tools is not None
        mode = self.modes.pop(0)
        if mode == "yes":
            self.answers.append(await tools.confirmation("yes"))
        elif mode != "plain":
            self.outcomes.append(await tools.run(ToolCall("c1", TOOL, {"level": 3})))
        size = 2 * AUDIO_CHUNK if mode == "cap" else AUDIO_CHUNK
        yield ReplyChunk(b"\0" * size, text="question")
        if mode == "moderation":
            raise TurnError("moderation")
        if mode == "question":
            await self.forever.wait()


class Refuse:
    """A turn limit that refuses every turn."""

    def reserve(self) -> float:
        return 1.0

    def cancel(self) -> None:
        pass


def make(*modes: str) -> tuple[DeviceSession, FakeSocket, FakeSleep, Lamp, Script]:
    ws, sleep, lamp, script = FakeSocket(), FakeSleep(), Lamp(), Script(*modes)
    session = DeviceSession(ws, script, mcp_sources=[lamp], sleep=sleep)  # type: ignore[arg-type]
    return session, ws, sleep, lamp, script


async def turn(session: DeviceSession, pcm: bytes = SPEECH) -> None:
    """listen.start, the audio, listen.stop, and the whole reply."""
    await session.handle_text({"type": "listen.start"})
    session.audio.extend(pcm)
    await session.handle_text({"type": "listen.stop", "turn": session.turn})
    assert session.reply_task is not None
    await session.reply_task
    await asyncio.sleep(0)  # the timer task starts


async def fire(session: DeviceSession, sleep: FakeSleep) -> None:
    sleep.released.set()
    assert session.confirm_timer is not None
    await session.confirm_timer


def timer_stopped(session: DeviceSession) -> bool:
    timer = session.confirm_timer
    return timer is None or timer.cancelled()


def test_message_order() -> None:
    async def scenario() -> FakeSocket:
        session, ws, *_ = make("hold")
        await turn(session)
        return ws

    msgs = asyncio.run(scenario()).json()
    end = next(i for i, m in enumerate(msgs) if m["type"] == "reply.end")
    assert msgs[end]["reason"] == "done"
    assert msgs[end + 1 :] == [
        {"type": "state", "turn": 1, "state": "confirm"},
        {"type": "confirm.request", "turn": 1, "tool": TOOL, "timeout_ms": 10000},
    ]


def test_timeout() -> None:
    async def scenario() -> tuple[FakeSocket, FakeSleep, Lamp, Script]:
        session, ws, sleep, lamp, script = make("hold", "yes")
        await turn(session)
        await fire(session, sleep)
        await turn(session)
        return ws, sleep, lamp, script

    ws, sleep, lamp, script = asyncio.run(scenario())
    assert sleep.delays == [10]
    assert {"type": "error", "turn": 1, "code": "confirm_timeout"} in ws.json()
    assert script.answers == [None]
    assert lamp.calls == []


@pytest.mark.parametrize("mode", ["cap", "moderation"])
def test_a_cut_reply_gives_no_request(mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_module, "SPEECH_CAP", AUDIO_CHUNK)

    async def scenario() -> tuple[DeviceSession, FakeSocket]:
        session, ws, *_ = make(mode)
        await turn(session)
        return session, ws

    session, ws = asyncio.run(scenario())
    reason = next(m["reason"] for m in ws.json() if m["type"] == "reply.end")
    assert reason == ("cap" if mode == "cap" else "error")
    assert "confirm.request" not in ws.types()
    assert session.hub.gate.holding() is None
    assert session.hub.gate.arm(1) is None  # nothing left to arm
    assert session.confirm_timer is None


def test_too_many_turns_keeps_the_call() -> None:
    async def scenario() -> tuple[FakeSocket, FakeSleep]:
        session, ws, sleep, *_ = make("hold")
        await turn(session)
        session.turn_limit = cast(TokenBucket, Refuse())
        await session.handle_text({"type": "listen.start"})
        assert not timer_stopped(session)
        await fire(session, sleep)
        return ws, sleep

    ws, sleep = asyncio.run(scenario())
    assert ws.codes() == ["too_many_turns", "confirm_timeout"]
    assert sleep.delays == [10]


def test_listen_start_claims_and_no_speech_cancels() -> None:
    async def scenario() -> tuple[DeviceSession, FakeSocket, Lamp]:
        session, ws, sleep, lamp, _ = make("hold")
        await turn(session)
        await turn(session, pcm=b"")
        assert timer_stopped(session)
        sleep.released.set()
        await asyncio.sleep(0)
        return session, ws, lamp

    session, ws, lamp = asyncio.run(scenario())
    assert ws.codes() == ["no_speech"]
    assert not session.hub.gate.claimed_by(2)
    assert lamp.calls == []


def test_a_claimed_call_without_listen_stop() -> None:
    async def scenario() -> tuple[DeviceSession, Lamp, Script]:
        session, _, _, lamp, script = make("hold", "yes")
        await turn(session)
        await session.handle_text({"type": "listen.start"})  # claims; no listen.stop
        assert session.hub.gate.claimed_by(2)
        await turn(session)
        return session, lamp, script

    session, lamp, script = asyncio.run(scenario())
    assert script.answers == [None]
    assert lamp.calls == []
    assert not session.hub.gate.claimed_by(2)


def test_abort() -> None:
    async def scenario() -> DeviceSession:
        session, *_ = make("hold")
        await turn(session)
        await session.handle_text({"type": "abort", "turn": session.turn})
        return session

    session = asyncio.run(scenario())
    assert session.hub.gate.claim(2) is None  # nothing armed
    assert timer_stopped(session)


@pytest.mark.parametrize("claimed", [False, True])
def test_close(claimed: bool) -> None:
    async def scenario() -> DeviceSession:
        session, *_ = make("hold")
        await turn(session)
        if claimed:
            await session.handle_text({"type": "listen.start"})
        await session.close()
        return session

    session = asyncio.run(scenario())
    assert not session.hub.gate.claimed_by(2)
    assert session.hub.gate.claim(3) is None
    assert timer_stopped(session)


def test_listen_start_after_the_timeout() -> None:
    async def scenario() -> tuple[FakeSocket, Script]:
        session, ws, sleep, _, script = make("hold", "yes")
        await turn(session)
        await fire(session, sleep)
        await turn(session)
        return ws, script

    ws, script = asyncio.run(scenario())
    assert script.answers == [None]
    ends = [m["reason"] for m in ws.json() if m["type"] == "reply.end"]
    assert ends == ["done", "done"]
    assert ws.types().count("confirm.request") == 1


def test_listen_start_during_the_question() -> None:
    async def scenario() -> tuple[DeviceSession, FakeSocket, Script]:
        session, ws, _, _, script = make("question", "hold")
        await session.handle_text({"type": "listen.start"})
        session.audio.extend(SPEECH)
        await session.handle_text({"type": "listen.stop", "turn": 1})
        while not script.outcomes:
            await asyncio.sleep(0)
        await turn(session)
        return session, ws, script

    session, ws, script = asyncio.run(scenario())
    assert [o.code for o in script.outcomes] == ["confirm_required", "confirm_required"]
    requests = [m for m in ws.json() if m["type"] == "confirm.request"]
    assert [m["turn"] for m in requests] == [2]
    assert session.hub.gate.claim(3) is not None  # the new call is armed


def test_a_claimed_call_expires_without_listen_stop() -> None:
    async def scenario() -> tuple[DeviceSession, FakeSleep, Lamp, Script]:
        session, _, sleep, lamp, script = make("hold", "yes")
        await turn(session)
        await session.handle_text({"type": "listen.start"})  # claims; no listen.stop
        await asyncio.sleep(0)  # the claim timer starts
        assert session.hub.gate.claimed_by(2)
        await fire(session, sleep)
        assert not session.hub.gate.claimed_by(2)
        session.audio.extend(SPEECH)  # a late listen.stop finds no call
        await session.handle_text({"type": "listen.stop", "turn": 2})
        assert session.reply_task is not None
        await session.reply_task
        return session, sleep, lamp, script

    _, sleep, lamp, script = asyncio.run(scenario())
    assert sleep.delays == [10, session_module.CLAIM_TIMEOUT_S]
    assert script.answers == [None]
    assert lamp.calls == []


def test_listen_stop_stops_the_claim_timer() -> None:
    async def scenario() -> tuple[Lamp, Script]:
        session, _, sleep, lamp, script = make("hold", "yes")
        await turn(session)
        await session.handle_text({"type": "listen.start"})
        await asyncio.sleep(0)
        assert session.confirm_timer is not None
        session.audio.extend(SPEECH)
        await session.handle_text({"type": "listen.stop", "turn": 2})
        assert session.confirm_timer is None
        sleep.released.set()  # a late clock tick must not drop the answer
        assert session.reply_task is not None
        await session.reply_task
        return lamp, script

    lamp, script = asyncio.run(scenario())
    assert script.answers == ["Done."]
    assert lamp.calls == [{"level": 3}]

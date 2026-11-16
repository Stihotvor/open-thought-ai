"""DeviceSession parts that the gateway tests cannot time: pacing, credit and cancel."""

import asyncio
import logging
import time

import pytest

from ota_server import session as session_module
from ota_server.protocol import AUDIO_CHUNK, MAX_CREDIT, MAX_LEAD_SECONDS
from ota_server.reply import Notify, ReplyChunk, ReplySource, no_notify
from ota_server.session import CreditStalled, DeviceSession, echo


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list = []

    async def send_json(self, msg: dict) -> None:
        self.sent.append(msg)

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)


def new_session() -> DeviceSession:
    return DeviceSession(FakeSocket())  # type: ignore[arg-type]


def test_late_audio_is_paced_from_now():
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


def test_credit_is_capped_at_the_device_buffer():
    session = new_session()
    asyncio.run(session.handle_text({"type": "audio.credit", "bytes": 10**9}))
    assert session.credit == MAX_CREDIT


def test_no_credit_ends_the_stream(monkeypatch):
    monkeypatch.setattr(session_module, "CREDIT_TIMEOUT", 0.05)
    session = new_session()
    with pytest.raises(CreditStalled):
        asyncio.run(session.stream(1, b"\0" * AUDIO_CHUNK, 0, time.monotonic()))


def test_cancel_logs_a_crashed_reply_task(caplog):
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


def test_abort_during_the_last_message_adds_no_second_end():
    async def scenario() -> list:
        session = new_session()
        session.open_turn = 1
        gate = asyncio.Event()
        sent = session.ws.sent

        async def slow_send(msg: dict) -> None:
            sent.append(msg)  # on the wire, but the send has not returned yet
            await gate.wait()

        session.ws.send_json = slow_send  # type: ignore[method-assign]
        session.reply_task = asyncio.create_task(
            session.finish({"type": "reply.end", "turn": 1, "reason": "done"})
        )
        await asyncio.sleep(0)  # the last message is being sent
        await session.cancel_reply()
        return sent

    ends = [m for m in asyncio.run(scenario()) if m.get("type") == "reply.end"]
    assert ends == [{"type": "reply.end", "turn": 1, "reason": "done"}]


def spoken_reply(texts: list[str], **session_args) -> DeviceSession:
    """Run one reply of 100 ms chunks through a session with plenty of credit."""

    async def source(pcm: bytes, history, *, notify: Notify = no_notify):
        for text in texts:
            yield ReplyChunk(b"\0" * AUDIO_CHUNK, text, "question")

    async def scenario() -> DeviceSession:
        session = DeviceSession(FakeSocket(), source, **session_args)  # type: ignore[arg-type]
        session.credit = MAX_CREDIT
        session.open_turn = 1
        await session.reply(1, b"\0" * AUDIO_CHUNK * 4)
        return session

    return asyncio.run(scenario())


def test_a_played_reply_is_remembered():
    session = spoken_reply(["One.", "Two."])
    assert list(session.history) == [("question", "One. Two.")]


def test_a_chunk_cut_by_the_cap_is_marked(monkeypatch):
    monkeypatch.setattr(session_module, "SPEECH_CAP", AUDIO_CHUNK + 1)
    session = spoken_reply(["One.", "Two.", "Three."])
    assert list(session.history) == [("question", "One. Two. …")]


def test_a_cut_first_chunk_keeps_the_question(monkeypatch):
    monkeypatch.setattr(session_module, "SPEECH_CAP", AUDIO_CHUNK // 2)
    session = spoken_reply(["A long story."])
    assert list(session.history) == [("question", "A long story. …")]


def test_history_turns_is_a_sliding_window():
    session = spoken_reply(["a."], history_turns=2)
    session.remember([ReplyChunk(b"", "b.", "q2")])
    session.remember([ReplyChunk(b"", "c.", "q3")])
    assert list(session.history) == [("q2", "b."), ("q3", "c.")]


def test_an_aborted_reply_keeps_only_the_played_chunks():
    async def scenario() -> DeviceSession:
        async def source(pcm: bytes, history, *, notify: Notify = no_notify):
            yield ReplyChunk(b"\0" * AUDIO_CHUNK, "One.", "question")
            yield ReplyChunk(b"\0" * AUDIO_CHUNK * 50, "Long.", "question")

        session = DeviceSession(FakeSocket(), source)  # type: ignore[arg-type]
        session.credit = MAX_CREDIT
        session.open_turn = 1
        session.reply_task = asyncio.create_task(session.reply(1, b"\0" * AUDIO_CHUNK * 4))
        while sum(isinstance(m, bytes) for m in session.ws.sent) < 2:
            await asyncio.sleep(0.01)  # wait until "Long." is playing
        await session.cancel_reply()
        return session

    assert list(asyncio.run(scenario()).history) == [("question", "One. Long. …")]


def test_echo_turns_leave_no_history():
    assert not spoken_reply([""]).history


def reply_messages(source: ReplySource = echo) -> list[dict]:
    """Run one reply with plenty of credit: the text messages the device gets."""

    async def scenario() -> list[dict]:
        socket = FakeSocket()
        session = DeviceSession(socket, source)  # type: ignore[arg-type]
        session.credit = MAX_CREDIT
        session.open_turn = 1
        await session.reply(1, b"\0" * AUDIO_CHUNK * 4)
        return [m for m in socket.sent if isinstance(m, dict)]

    return asyncio.run(scenario())


def transcript_turn(text: str) -> list[dict]:
    """Run one reply whose source calls notify(text), then yields reply audio."""

    async def source(pcm: bytes, history, *, notify: Notify = no_notify):
        await notify(text)
        yield ReplyChunk(b"\0" * AUDIO_CHUNK, "Hi.", text)

    return reply_messages(source)


def test_transcript_is_sent_before_the_reply():
    msgs = transcript_turn("hello")
    assert {"type": "transcript", "turn": 1, "text": "hello"} in msgs
    types = [m["type"] for m in msgs]
    assert types.index("transcript") < types.index("reply.start")


def test_transcript_text_is_truncated():
    msgs = transcript_turn("a" * 300)
    assert [m["text"] for m in msgs if m["type"] == "transcript"] == ["a" * 200]


def test_no_transcript_after_abort():
    async def scenario() -> list:
        notifiers: list[Notify] = []
        waiting = asyncio.Event()

        async def source(pcm: bytes, history, *, notify: Notify = no_notify):
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


def test_echo_sends_no_transcript():
    msgs = reply_messages()
    assert msgs[-1] == {"type": "reply.end", "turn": 1, "reason": "done"}
    assert "transcript" not in [m["type"] for m in msgs]

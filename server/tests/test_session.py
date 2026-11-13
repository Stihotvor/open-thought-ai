"""DeviceSession parts that the gateway tests cannot time: pacing, credit and cancel."""

import asyncio
import logging
import time

import pytest

from ota_server import session as session_module
from ota_server.protocol import AUDIO_CHUNK, MAX_CREDIT
from ota_server.session import CreditStalled, DeviceSession


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
        clock = await session.stream(1, b"\0" * AUDIO_CHUNK * 6, 0, late_clock)
        return clock, late_clock, time.monotonic() - started

    clock, late_clock, took = asyncio.run(scenario())
    assert clock > late_clock + 4.5  # paced again, not sent as one 5 s burst
    assert took >= 0.15  # 600 ms of audio, at most 300 ms ahead: about 200 ms of waits


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

"""QA cases for VAD and transcript (docs/design/vad-transcript/qa/cases.md), fake providers only."""

import asyncio
import random
import time
from array import array

import pytest
from test_pipeline import SILENCE, SPEECH, Endpoint, pipeline_for, square, tts_wav

from ota_server.protocol import (
    BYTES_PER_SECOND,
    MAX_CREDIT,
    MAX_TURN_AUDIO,
    SAMPLE_RATE,
    TRANSCRIPT_MAX_CHARS,
)
from ota_server.session import DeviceSession


class Socket(list):
    async def send_json(self, msg: dict) -> None:
        self.append(msg)

    async def send_bytes(self, data: bytes) -> None:
        self.append(data)


def sil(ms: int) -> bytes:
    return bytes(SAMPLE_RATE * ms // 1000 * 2)


def tone(ms: int, amp: int = 8000) -> bytes:
    return square(ms / 1000, amp)


def noise(ms: int, amp: int = 300) -> bytes:
    rng = random.Random(1)
    return array("h", [rng.randint(-amp, amp) for _ in range(SAMPLE_RATE * ms // 1000)]).tobytes()


def make(endpoint: Endpoint, turn: int = 1) -> DeviceSession:
    session = DeviceSession(Socket(), pipeline_for(endpoint))  # type: ignore[arg-type]
    session.credit, session.open_turn, session.turn = MAX_CREDIT, turn, turn
    return session


def turn_msgs(endpoint: Endpoint, pcm: bytes, turn: int = 1) -> list:
    async def go() -> list:
        session = make(endpoint, turn)
        await session.reply(turn, pcm)
        return list(session.ws)

    return asyncio.run(go())


def texts(msgs: list) -> list[str]:
    return [m["text"] for m in msgs if isinstance(m, dict) and m["type"] == "transcript"]


def codes(msgs: list) -> list[str]:
    return [m["code"] for m in msgs if isinstance(m, dict) and m["type"] == "error"]


def stt_calls(endpoint: Endpoint) -> int:
    return endpoint.calls.count("transcriptions")


def test_qa_002_margin_kept(monkeypatch):
    from ota_server import pipeline as p

    sizes: list[int] = []
    real = p.pcm_to_wav
    monkeypatch.setattr(p, "pcm_to_wav", lambda pcm: sizes.append(len(pcm)) or real(pcm))
    turn_msgs(Endpoint(), sil(5000) + tone(1000) + sil(5000))
    assert BYTES_PER_SECOND < sizes[0] <= 1.5 * BYTES_PER_SECOND


@pytest.mark.parametrize(
    ("qa", "pcm"),
    [
        ("003", sil(3000)),
        ("004", noise(3000)),
        ("005", noise(30000)),
        ("006", sil(1000) + tone(20, 20000) + sil(2000)),
        ("007", sil(1000) + tone(250) + sil(1000)),
        ("010", tone(100) + sil(1000) + tone(100) + sil(500)),
        ("012", sil(500) + tone(1000, 400) + sil(500)),
        ("013", b""),
    ],
)
def test_qa_no_speech(qa, pcm):
    endpoint = Endpoint()
    msgs = turn_msgs(endpoint, pcm)
    assert {"type": "error", "turn": 1, "code": "no_speech"} in msgs, qa
    assert stt_calls(endpoint) == 0


@pytest.mark.parametrize(
    "pcm",
    [
        sil(1000) + tone(400) + sil(1000),  # 008
        tone(200) + sil(1000) + tone(200),  # 009
        sil(1000) + tone(1000, 1500) + sil(1000),  # 011
        sil(1000) + tone(1000, 700) + sil(1000),  # 041
    ],
    ids=["008", "009", "011", "041"],
)
def test_qa_speech_reaches_stt(pcm):
    endpoint = Endpoint()
    turn_msgs(endpoint, pcm)
    assert stt_calls(endpoint) == 1


def test_qa_012_repeat_works_and_session_stays_open():
    endpoint = Endpoint(tts=tts_wav(0.05))

    async def go() -> list:
        session = make(endpoint)
        await session.reply(1, sil(500) + tone(1000, 400) + sil(500))
        session.open_turn = 2
        await session.reply(2, sil(500) + tone(1000) + sil(500))
        return list(session.ws)

    msgs = asyncio.run(go())
    assert codes(msgs) == ["no_speech"]
    assert {"type": "reply.end", "turn": 2, "reason": "done"} in msgs


def test_qa_014_015_odd_bytes_internal_then_next_turn_works():
    endpoint = Endpoint(tts=tts_wav(0.05))

    async def go() -> list:
        session = make(endpoint)
        await session.reply(1, SPEECH + b"\x00")
        assert stt_calls(endpoint) == 0
        session.open_turn = 2
        await session.reply(2, SPEECH)
        return list(session.ws)

    msgs = asyncio.run(go())
    assert {"type": "error", "turn": 1, "code": "internal"} in msgs
    assert texts(msgs) == ["Jak się masz?"]
    assert {"type": "reply.end", "turn": 2, "reason": "done"} in msgs


def test_qa_017_transcript_turn_id():
    msgs = turn_msgs(Endpoint(transcript="hello", tts=tts_wav(0.05)), SPEECH, turn=7)
    assert {"type": "transcript", "turn": 7, "text": "hello"} in msgs


@pytest.mark.parametrize("n", [TRANSCRIPT_MAX_CHARS, TRANSCRIPT_MAX_CHARS + 1])
def test_qa_020_021_boundary_lengths(n):
    msgs = turn_msgs(Endpoint(transcript="c" * n, tts=tts_wav(0.05)), SPEECH)
    assert texts(msgs) == ["c" * TRANSCRIPT_MAX_CHARS]


def test_qa_022_multibyte_transcript():
    text = ("привіт" * 50)[:250]
    msgs = turn_msgs(Endpoint(transcript=text, tts=tts_wav(0.05)), SPEECH)
    assert texts(msgs) == [text[:TRANSCRIPT_MAX_CHARS]]
    import json

    wire = json.dumps(
        {"type": "transcript", "turn": 1, "text": texts(msgs)[0]}, ensure_ascii=False
    ).encode()
    assert len(wire) < 1024


def test_qa_023_024_empty_and_blank_stt():
    for blank in ("", "   "):
        msgs = turn_msgs(Endpoint(transcript=blank), SPEECH)
        assert codes(msgs) == ["no_speech"]
        assert texts(msgs) == []


def test_qa_025_flagged_no_transcript_no_chat():
    endpoint = Endpoint(transcript="flagged phrase", flag=lambda t: "flagged" in t or "zabić" in t)
    msgs = turn_msgs(endpoint, SPEECH)
    assert codes(msgs) == ["moderated"]
    assert texts(msgs) == []
    assert "completions" not in endpoint.calls


def test_qa_026_027_039_abort_during_stt_then_new_turn(monkeypatch):
    """The late STT of the aborted turn sends nothing; the next turn gets its own transcript."""
    from ota_server import pipeline as p

    delays = {"n": 0}
    real = p.VoicePipeline.__call__
    endpoint = Endpoint(transcript="old", tts=tts_wav(0.05))
    pipe = pipeline_for(endpoint)

    async def slow_call(self, pcm, history, *, notify):
        delays["n"] += 1
        if delays["n"] == 1:

            async def late(text: str) -> None:
                await asyncio.sleep(0.4)  # STT "returns" after the abort
                await notify(text)

            async for chunk in real(self, pcm, history, notify=late):
                yield chunk
        else:
            endpoint.transcript = "second"
            async for chunk in real(self, pcm, history, notify=notify):
                yield chunk

    monkeypatch.setattr(p.VoicePipeline, "__call__", slow_call)

    async def go() -> list:
        session = DeviceSession(Socket(), pipe)  # type: ignore[arg-type]
        await session.handle_text({"type": "listen.start"})
        session.audio.extend(SPEECH)
        await session.handle_text({"type": "listen.stop", "turn": 1})
        await asyncio.sleep(0.1)
        await session.handle_text({"type": "abort", "turn": 1})
        await asyncio.sleep(0.6)
        await session.handle_text({"type": "listen.start"})
        session.credit = MAX_CREDIT
        session.audio.extend(SPEECH)
        await session.handle_text({"type": "listen.stop", "turn": 2})
        await session.reply_task  # type: ignore[misc]
        return list(session.ws)

    msgs = asyncio.run(go())
    assert {"type": "reply.end", "turn": 1, "reason": "abort"} in msgs
    assert [
        (m["turn"], m["text"]) for m in msgs if isinstance(m, dict) and m["type"] == "transcript"
    ] == [(2, "second")]


def test_qa_028_closed_socket_at_transcript_send():
    class Closed(Socket):
        async def send_json(self, msg: dict) -> None:
            if msg["type"] == "transcript":
                raise ConnectionError("closed")
            await super().send_json(msg)

    async def go() -> None:
        session = DeviceSession(Closed(), pipeline_for(Endpoint()))  # type: ignore[arg-type]
        session.credit, session.open_turn = MAX_CREDIT, 1
        session.reply_task = asyncio.create_task(session.reply(1, SPEECH))
        await asyncio.wait([session.reply_task])
        await session.cancel_reply()  # logs, must not raise
        # a next connection can run a turn
        msgs = await asyncio.to_thread(turn_msgs, Endpoint(tts=tts_wav(0.05)), SPEECH)
        assert texts(msgs)

    asyncio.run(go())


def test_qa_029_transcript_before_llm_error_and_none_after():
    msgs = turn_msgs(Endpoint(fail={"/v1/chat/completions": 500}), SPEECH)
    kinds = [m["type"] for m in msgs]
    assert kinds.index("transcript") < kinds.index("error")
    assert kinds[-1] == "error" and codes(msgs) == ["llm_failed"]


def test_qa_030_repeated_listen_stop_same_turn_is_ignored():
    async def go() -> list:
        session = DeviceSession(Socket(), pipeline_for(Endpoint(tts=tts_wav(0.05))))  # type: ignore[arg-type]
        await session.handle_text({"type": "listen.start"})
        session.audio.extend(SPEECH)
        await session.handle_text({"type": "listen.stop", "turn": 1})
        await session.reply_task  # type: ignore[misc]
        before = len(session.ws)
        await session.handle_text({"type": "listen.stop", "turn": 1})
        assert session.reply_task.done()  # type: ignore[union-attr]
        await asyncio.sleep(0.1)
        assert len(session.ws) == before
        return list(session.ws)

    assert len(texts(asyncio.run(go()))) == 1


def test_qa_032_vad_does_not_block_the_event_loop():
    pcm = (noise(1000) + tone(1000)) * 15
    assert len(pcm) <= MAX_TURN_AUDIO

    async def go() -> float:
        gaps: list[float] = []
        stop = False

        async def ticker() -> None:
            last = time.monotonic()
            while not stop:
                await asyncio.sleep(0.01)
                now = time.monotonic()
                gaps.append(now - last)
                last = now

        task = asyncio.create_task(ticker())
        await asyncio.sleep(0.05)
        await asyncio.to_thread(turn_msgs, Endpoint(tts=tts_wav(0.05)), pcm)
        stop = True
        await task
        return max(gaps)

    assert asyncio.run(go()) < 0.1


def test_qa_038_no_stt_without_listen_stop():
    endpoint = Endpoint()

    async def go() -> None:
        session = DeviceSession(Socket(), pipeline_for(endpoint))  # type: ignore[arg-type]
        await session.handle_text({"type": "listen.start"})
        session.handle_audio(bytes([1]) + (tone(1000) + sil(5000))[:3200])
        await asyncio.sleep(0.3)
        assert session.reply_task is None

    asyncio.run(go())
    assert endpoint.calls == []


def test_qa_033_echo_one_second_no_transcript():
    async def go() -> list:
        session = DeviceSession(Socket())  # type: ignore[arg-type]
        session.credit, session.open_turn = MAX_CREDIT, 1
        await session.reply(1, SILENCE[: BYTES_PER_SECOND // 2])
        return list(session.ws)

    msgs = asyncio.run(go())
    assert sum(len(m) - 1 for m in msgs if isinstance(m, bytes)) == BYTES_PER_SECOND // 2
    assert texts(msgs) == []

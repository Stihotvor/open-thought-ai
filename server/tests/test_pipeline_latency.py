"""Stage times of a voice turn: one `turn.latency` line per turn (T2)."""

import asyncio
import logging
import time

import pytest
from fakes import SPEECH, Endpoint, pipeline_for

from ota_server import pipeline as pipeline_module
from ota_server.pipeline import VoicePipeline
from ota_server.protocol import BYTES_PER_SECOND, TurnError
from ota_server.timing import StageTimer, TurnTiming, parse_line

GOOD_STAGES = {
    "vad",
    "stt",
    "mod_transcript",
    "chat_first_sentence",
    "chat_full",
    "mod_reply",
    "tts1",
}


async def drain(pipeline: VoicePipeline) -> bytes:
    return b"".join([chunk.pcm async for chunk in pipeline(SPEECH, ())])


def turns(endpoint: Endpoint, count: int = 1) -> list[bytes]:
    """Run count turns on one pipeline. Return the audio of each (b"" when refused)."""
    pipeline = pipeline_for(endpoint)
    out: list[bytes] = []
    for _ in range(count):
        try:
            out.append(asyncio.run(drain(pipeline)))
        except TurnError:
            out.append(b"")
    return out


def lines(caplog: pytest.LogCaptureFixture) -> list[TurnTiming]:
    parsed = [parse_line(r.getMessage()) for r in caplog.records if r.levelno == logging.INFO]
    return [p for p in parsed if p is not None]


@pytest.fixture(autouse=True)
def info_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)


def test_a_good_turn_logs_all_stages(caplog: pytest.LogCaptureFixture) -> None:
    audio = turns(Endpoint(reply="Dobrze. A ty?"))
    assert len(audio[0]) == BYTES_PER_SECOND
    [line] = lines(caplog)
    assert line.code == "ok"
    assert line.turn_id == 1
    assert line.latency_ms is not None
    assert set(line.stages) >= GOOD_STAGES
    assert all(ms >= 0 for ms in line.stages.values())


def test_chat_first_sentence_is_within_chat_full(caplog: pytest.LogCaptureFixture) -> None:
    turns(Endpoint(reply="Dobrze. A ty?"))
    [line] = lines(caplog)
    assert line.stages["chat_first_sentence"] <= line.stages["chat_full"]


def test_selftest_shows_on_the_first_turn_only(caplog: pytest.LogCaptureFixture) -> None:
    turns(Endpoint(), count=2)
    first, second = lines(caplog)
    assert (first.turn_id, second.turn_id) == (1, 2)
    assert "selftest" in first.stages
    assert "selftest" not in second.stages


def test_a_failed_turn_logs_its_code(caplog: pytest.LogCaptureFixture) -> None:
    audio = turns(Endpoint(fail={"/v1/chat/completions": 500}))
    assert audio == [b""]
    [line] = lines(caplog)
    assert line.code == "llm_failed"
    assert line.latency_ms is None
    assert {"vad", "stt", "mod_transcript"} <= set(line.stages)
    assert "chat_full" not in line.stages


def test_a_turn_refused_before_any_provider_call_logs_its_code(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pipeline = pipeline_for(Endpoint())

    async def silent() -> None:
        async for _ in pipeline(bytes(BYTES_PER_SECOND), ()):
            pass

    with pytest.raises(TurnError):
        asyncio.run(silent())
    [line] = lines(caplog)
    assert line.code == "no_speech"
    assert line.latency_ms is None


def test_no_text_in_the_log(caplog: pytest.LogCaptureFixture) -> None:
    turns(Endpoint(transcript="SECRET-Q", reply="SECRET-A. Two."))
    assert len(lines(caplog)) == 1
    for record in caplog.records:
        if record.levelno == logging.INFO:
            assert "SECRET-Q" not in record.getMessage()
            assert "SECRET-A" not in record.getMessage()


def test_a_reply_without_a_sentence_end(caplog: pytest.LogCaptureFixture) -> None:
    audio = turns(Endpoint(reply="ok"))
    assert len(audio[0]) == BYTES_PER_SECOND // 2
    [line] = lines(caplog)
    assert line.code == "ok"
    assert "chat_first_sentence" not in line.stages
    assert "chat_full" in line.stages


class RaisingTimer(StageTimer):
    def start(self) -> None:
        raise RuntimeError("start")

    def mark(self, stage: str) -> None:
        raise RuntimeError(stage)

    def latency_ms(self) -> int | None:
        raise RuntimeError("latency")


def test_a_timer_error_does_not_break_a_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline_module, "StageTimer", RaisingTimer)
    audio = turns(Endpoint(reply="Dobrze. A ty?"))
    assert len(audio[0]) == BYTES_PER_SECOND


def test_a_turn_closed_after_the_first_chunk_logs_one_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pipeline = pipeline_for(Endpoint(reply="Hi. Two. Three."))

    async def first_chunk() -> None:
        chunks = pipeline(SPEECH, ())
        await anext(chunks)
        await chunks.aclose()

    asyncio.run(first_chunk())
    [line] = lines(caplog)
    assert line.code == "closed"
    assert line.latency_ms is not None
    assert "tts1" in line.stages


class NullClock:
    """A TurnClock that does nothing: the turn without timing."""

    def __init__(self, turn_id: int) -> None:
        self.code = "ok"

    def mark(self, stage: str) -> None:
        pass

    def chat_started(self) -> None:
        pass

    def chat_text(self, text: str) -> None:
        pass

    def first_audio(self) -> None:
        pass

    def log(self) -> None:
        pass


def fastest_turn(pipeline: VoicePipeline, runs: int) -> float:
    best = float("inf")
    for _ in range(runs):
        start = time.perf_counter()
        asyncio.run(drain(pipeline))
        best = min(best, time.perf_counter() - start)
    return best


def test_overhead_is_one_ms_or_less(monkeypatch: pytest.MonkeyPatch) -> None:
    timed, plain = pipeline_for(Endpoint()), pipeline_for(Endpoint())
    real_clock = pipeline_module.TurnClock
    timed_best, plain_best = float("inf"), float("inf")
    for _ in range(10):  # interleaved, so machine load hits both the same
        monkeypatch.setattr(pipeline_module, "TurnClock", real_clock)
        timed_best = min(timed_best, fastest_turn(timed, 3))
        monkeypatch.setattr(pipeline_module, "TurnClock", NullClock)
        plain_best = min(plain_best, fastest_turn(plain, 3))
    assert (timed_best - plain_best) * 1000 <= 1

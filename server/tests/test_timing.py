import logging

import pytest

from ota_server.timing import STAGES, StageTimer, TurnTiming, format_line, parse_line


def test_line_round_trip() -> None:
    stages = {name: i * 10 + 5 for i, name in enumerate(STAGES)}
    line = format_line(7, "ok", 4200, stages)
    assert parse_line(line) == TurnTiming(turn_id=7, code="ok", latency_ms=4200, stages=stages)


def test_failed_turn_has_no_latency() -> None:
    parsed = parse_line(format_line(3, "llm_failed", None, {"vad": 90, "stt": 300}))
    assert parsed is not None
    assert parsed.latency_ms is None
    assert parsed.code == "llm_failed"
    assert parsed.stages == {"vad": 90, "stt": 300}


def test_stages_is_complete() -> None:
    assert STAGES == (
        "vad",
        "stt",
        "mod_transcript",
        "chat_first_sentence",
        "chat_full",
        "mod_reply",
        "tts1",
        "selftest",
    )


def test_timer_error_does_not_raise(caplog: pytest.LogCaptureFixture) -> None:
    def broken_clock() -> float:
        raise RuntimeError("clock down")

    timer = StageTimer(clock=broken_clock)
    with caplog.at_level(logging.ERROR, logger="ota_server.timing"):
        timer.mark("stt")
    assert len(caplog.records) == 1
    assert timer.stages == {}


def test_other_lines_are_ignored() -> None:
    assert parse_line("hello world") is None

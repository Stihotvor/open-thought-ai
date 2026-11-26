"""Tests of the latency report (T6) on a fixed sample JSONL.

Expected percentiles are computed by hand with linear interpolation between closest
ranks (statistics.quantiles, method="inclusive"): for sorted values x[0..n-1] and
p in (0, 1), position h = (n - 1) * p, value = x[floor(h)] + (h - floor(h)) * step.
Example, en vad [100, 200, 300, 400]: p50 h = 1.5 -> 250, p95 h = 2.85 -> 385.
"""

import json
import re
from pathlib import Path

import pytest

from ota_server.latency_report import (
    StageStat,
    build_report,
    main,
    summarize,
    wer,
)
from ota_server.timing import STAGES

SAMPLE = Path(__file__).parent / "data" / "latency_sample.jsonl"

PHRASES = json.dumps(
    {
        "phrases": [
            {"id": "p01", "en": "Hello world.", "pl": "Cześć, świecie!", "uk": "Привіт, світе!"},
            {"id": "p02", "en": "Good morning, Sam!", "pl": "Dzień dobry.", "uk": "Добрий ранок."},
            {"id": "p03", "en": "What time is it?", "pl": "Która godzina?", "uk": "Котра година?"},
            {"id": "p04", "en": "Tell me a joke.", "pl": "Opowiedz żart.", "uk": "Розкажи жарт."},
            {"id": "p05", "en": "Where is Kraków?", "pl": "Gdzie jest Kraków?", "uk": "Де Краків?"},
            {"id": "p06", "en": "Is it cold today?", "pl": "Czy jest zimno?", "uk": "Чи холодно?"},
        ]
    }
)

# (p50, p95) per stage, computed by hand from the sample (see module docstring).
EXPECTED_EN = {
    "vad": (250, 385),
    "stt": (1300, 1570),
    "chat_first_sentence": (1500, 1950),
    "chat_full": (2750, 3850),
    "tts1": (850, 985),
}
EXPECTED_PL = {
    "vad": (200, 245),
    "stt": (1200, 1290),
    "chat_first_sentence": (2000, 2180),
    "chat_full": (3000, 3360),
    "tts1": (800, 845),
}


def sample_text() -> str:
    return SAMPLE.read_text(encoding="utf-8")


def record(turn_id: int, code: str = "ok", latency: int | None = 4000, **stages: int) -> str:
    return json.dumps(
        {
            "run": "r",
            "lang": "en",
            "phrase_id": "p01",
            "turn_id": turn_id,
            "code": code,
            "latency_ms": latency,
            "stages": stages,
            "stt_text": None,
        }
    )


def pair(stat: StageStat) -> tuple[float | None, float | None]:
    return (stat.p50, stat.p95)


def test_exact_percentiles_per_stage_and_language() -> None:
    summaries = {s.lang: s for s in summarize(sample_text(), PHRASES)}
    assert set(summaries) == {"en", "pl"}
    for lang, expected in (("en", EXPECTED_EN), ("pl", EXPECTED_PL)):
        stages = summaries[lang].stages
        assert {name: pair(stages[name]) for name in expected} == expected
    assert pair(summaries["en"].latency) == (4000, 4000)
    assert pair(summaries["pl"].latency) == (5200, 5560)


def test_report_shows_the_fixed_values() -> None:
    report = build_report(sample_text(), PHRASES)
    assert re.search(r"\| vad \| 4 \| 0 \| 250 \| 385 \|", report)
    assert re.search(r"\| chat_first_sentence \| 3 \| 1 \| 1500 \| 1950 \|", report)
    assert re.search(r"\| stt \| 2 \| 0 \| 1200 \| 1290 \|", report)
    assert 'method="inclusive"' in report


def test_warm_up_left_out() -> None:
    lines = [record(1, latency=9000)] + [record(i, latency=4000) for i in range(2, 6)]
    summary = summarize("\n".join(lines), None)[0]
    assert summary.latency.p50 == 4000
    assert summary.latency.n == 4
    assert summary.warmup == 1


def test_failures_counted_and_excluded() -> None:
    lines = [
        record(1, latency=9000, vad=900),
        record(2, latency=4000, vad=100),
        record(3, code="no_speech", latency=None, vad=5000),
        record(4, code="rate_limited", latency=None, vad=5000),
        record(5, latency=4000, vad=100),
    ]
    summary = summarize("\n".join(lines), None)[0]
    assert dict(summary.failures) == {"no_speech": 1, "rate_limited": 1}
    assert summary.stages["vad"].n == 2
    assert pair(summary.stages["vad"]) == (100, 100)
    report = build_report("\n".join(lines), None)
    assert "| no_speech | 1 |" in report
    assert "| rate_limited | 1 |" in report


def test_sample_failures() -> None:
    summary = {s.lang: s for s in summarize(sample_text(), PHRASES)}["en"]
    assert dict(summary.failures) == {"no_speech": 1, "rate_limited": 1}
    assert summary.ok == 4


def test_missing_stage_counted() -> None:
    lines = [
        record(1, vad=1, chat_first_sentence=1),
        record(2, vad=1, chat_first_sentence=1000),
        record(3, vad=1),
    ]
    summary = summarize("\n".join(lines), None)[0]
    assert summary.stages["chat_first_sentence"].missing == 1
    assert re.search(r"\| chat_first_sentence \| 1 \| 1 \|", build_report("\n".join(lines), None))


def test_budget_footnote_notes_and_not_measured() -> None:
    report = build_report(sample_text(), PHRASES)
    budgets = {
        "vad": "0.2 s",
        "stt": "1.2 s",
        "chat_first_sentence": "2.0 s",
        "tts1": "0.8 s",
    }
    for stage, budget in budgets.items():
        row = next(line for line in report.splitlines() if line.startswith(f"| {stage} |"))
        assert budget in row
    assert "streams per sentence" in report
    assert "synthetic" in report and "Piper" in report
    upload = next(line for line in report.splitlines() if line.startswith("| Upload tail"))
    network = next(line for line in report.splitlines() if line.startswith("| Network + device"))
    assert "not measured" in upload
    assert "not measured" in network and "0.3 s" in network
    for stage in STAGES:
        assert any(line.startswith(f"| {stage} |") for line in report.splitlines())


def test_streaming_gap() -> None:
    lines = [record(1), record(2, chat_first_sentence=1000, chat_full=3000)]
    summary = summarize("\n".join(lines), None)[0]
    assert pair(summary.saving) == (2000, 2000)
    report = build_report("\n".join(lines), None)
    row = next(line for line in report.splitlines() if "overlaps audio" in line)
    assert "2000" in row


def test_sample_streaming_gap() -> None:
    summaries = {s.lang: s for s in summarize(sample_text(), PHRASES)}
    assert pair(summaries["en"].saving) == (2000, 2000)
    assert pair(summaries["pl"].saving) == (1000, 1180)


def test_wer_half() -> None:
    assert wer("hello world", "hello wor") == (1, 2)
    lines = [
        record(1),
        json.dumps(
            {
                "run": "r",
                "lang": "en",
                "phrase_id": "p01",
                "turn_id": 2,
                "code": "ok",
                "latency_ms": 4000,
                "stages": {},
                "stt_text": "hello wor",
            }
        ),
    ]
    summary = summarize("\n".join(lines), PHRASES)[0]
    assert summary.wer == 0.5
    assert re.search(r"WER[^\n]*0\.500", build_report("\n".join(lines), PHRASES))


def test_wer_normalises_case_and_punctuation() -> None:
    assert wer("Good morning, Sam!", "good MORNING sam") == (0, 3)
    assert wer("Dzień dobry.", "dzień dobry pan") == (1, 2)


def test_sample_wer() -> None:
    summaries = {s.lang: s for s in summarize(sample_text(), PHRASES)}
    assert summaries["en"].wer == pytest.approx(1 / 13)
    assert summaries["pl"].wer == pytest.approx(0.25)


def test_wer_without_phrases() -> None:
    summary = summarize(sample_text(), None)[0]
    assert summary.wer is None
    assert "WER: not computed" in build_report(sample_text(), None)


def test_p50_against_target() -> None:
    report = build_report(sample_text(), PHRASES)
    lines = report.splitlines()
    en = next(line for line in lines if line.startswith("p50 latency_ms") and "4000" in line)
    pl = next(line for line in lines if line.startswith("p50 latency_ms") and "5200" in line)
    assert "meets" in en
    assert "misses" in pl


def test_single_value_percentile() -> None:
    summary = summarize("\n".join([record(1), record(2, latency=4100)]), None)[0]
    assert pair(summary.latency) == (4100, 4100)


def test_main_writes_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    phrases = tmp_path / "phrases.json"
    phrases.write_text(PHRASES, encoding="utf-8")
    assert main([str(SAMPLE), "--phrases", str(phrases)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Voice latency report")
    assert "## en" in out and "## pl" in out

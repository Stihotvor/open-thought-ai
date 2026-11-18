"""Latency report (T6): p50 and p95 per stage and language from a latency run JSONL.

Usage: python -m ota_server.latency_report <run.jsonl> [--phrases m3-phrases.json] > report.md

Input is the run record of decision.md, one JSON object per line:
{"run", "lang", "phrase_id", "turn_id", "code", "latency_ms", "stages", "stt_text"}.

Rules:
- Warm-up: the first record of each run value is left out of all numbers (F3).
- Numbers (percentiles, streaming gap, WER) use only turns with code "ok" (E1).
  Failures are counted by code over all turns, warm-up included.
- A stage that is absent from an ok turn counts as missing for that stage (E3).
- Percentiles: linear interpolation between closest ranks, as statistics.quantiles
  with method="inclusive". With one value, that value.
- WER: word edit distance over reference words, summed per language. Text is lower
  case, characters other than letters, digits and spaces are dropped, split on spaces.

All functions except main work on strings, so tests need no files.
"""

import argparse
import json
import statistics
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ota_server.timing import STAGES

TARGET_MS = 5000
OK = "ok"
METHOD = 'linear interpolation between closest ranks (statistics.quantiles, method="inclusive")'
BUDGET = {
    "vad": "0.2 s (Upload tail + VAD end)",
    "stt": "1.2 s",
    "chat_first_sentence": "2.0 s",
    "mod_reply": "0.5 s",
    "tts1": "0.8 s",
}
NO_BUDGET = "no budget row"


@dataclass(frozen=True)
class Record:
    run: str
    lang: str
    phrase_id: str
    code: str
    latency_ms: int | None
    stages: dict[str, int]
    stt_text: str | None


@dataclass(frozen=True)
class StageStat:
    n: int
    missing: int
    p50: float | None
    p95: float | None


@dataclass
class LanguageSummary:
    lang: str
    turns: int = 0
    warmup: int = 0
    ok: int = 0
    failures: Counter[str] = field(default_factory=Counter)
    stages: dict[str, StageStat] = field(default_factory=dict)
    latency: StageStat = StageStat(0, 0, None, None)
    saving: StageStat = StageStat(0, 0, None, None)
    wer: float | None = None
    wer_turns: int = 0


def parse_records(text: str) -> list[Record]:
    """Read the run JSONL. Blank lines are skipped."""
    return [_record(json.loads(line)) for line in text.splitlines() if line.strip()]


def _record(data: dict[str, Any]) -> Record:
    latency = data.get("latency_ms")
    stt_text = data.get("stt_text")
    return Record(
        run=str(data.get("run", "")),
        lang=str(data.get("lang", "")),
        phrase_id=str(data.get("phrase_id", "")),
        code=str(data.get("code", "")),
        latency_ms=None if latency is None else int(latency),
        stages={str(k): int(v) for k, v in (data.get("stages") or {}).items()},
        stt_text=None if stt_text is None else str(stt_text),
    )


def parse_phrases(text: str) -> dict[str, dict[str, str]]:
    """Map phrase id to {lang: text} from the m3-phrases.json format."""
    entries = json.loads(text)["phrases"]
    return {
        str(entry["id"]): {k: str(v) for k, v in entry.items() if k != "id"} for entry in entries
    }


def percentiles(values: Sequence[float]) -> tuple[float | None, float | None]:
    """p50 and p95 of values, see METHOD. Empty gives (None, None)."""
    if not values:
        return None, None
    if len(values) == 1:
        return float(values[0]), float(values[0])
    cuts = statistics.quantiles(values, n=100, method="inclusive")
    return cuts[49], cuts[94]


def stat(values: Sequence[float], missing: int = 0) -> StageStat:
    p50, p95 = percentiles(values)
    return StageStat(n=len(values), missing=missing, p50=p50, p95=p95)


def words(text: str) -> list[str]:
    kept = "".join(ch for ch in text.lower() if ch.isalnum() or ch.isspace())
    return kept.split()


def wer(reference: str, hypothesis: str) -> tuple[int, int]:
    """Word edits and reference word count."""
    ref, hyp = words(reference), words(hypothesis)
    row = list(range(len(hyp) + 1))
    for i, ref_word in enumerate(ref, start=1):
        prev, row[0] = row[0], i
        for j, hyp_word in enumerate(hyp, start=1):
            cost = 0 if ref_word == hyp_word else 1
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + cost)
    return row[-1], len(ref)


def split_warmup(records: Iterable[Record]) -> tuple[list[Record], int]:
    """Records without the first of each run, and the number left out."""
    seen: set[str] = set()
    kept: list[Record] = []
    for rec in records:
        if rec.run in seen:
            kept.append(rec)
        seen.add(rec.run)
    return kept, len(seen)


def stage_stats(turns: Sequence[Record]) -> dict[str, StageStat]:
    result: dict[str, StageStat] = {}
    for name in STAGES:
        values = [t.stages[name] for t in turns if name in t.stages]
        result[name] = stat(values, missing=len(turns) - len(values))
    return result


def saving_stat(turns: Sequence[Record]) -> StageStat:
    """chat_full minus chat_first_sentence per turn that has both."""
    gaps = [
        t.stages["chat_full"] - t.stages["chat_first_sentence"]
        for t in turns
        if "chat_full" in t.stages and "chat_first_sentence" in t.stages
    ]
    return stat(gaps, missing=len(turns) - len(gaps))


def language_wer(
    turns: Sequence[Record], phrases: dict[str, dict[str, str]] | None
) -> tuple[float | None, int]:
    if phrases is None:
        return None, 0
    edits = ref_words = used = 0
    for turn in turns:
        reference = phrases.get(turn.phrase_id, {}).get(turn.lang)
        if reference is None or turn.stt_text is None:
            continue
        errors, count = wer(reference, turn.stt_text)
        edits, ref_words, used = edits + errors, ref_words + count, used + 1
    return (edits / ref_words if ref_words else None), used


def summarize_language(
    lang: str, records: Sequence[Record], phrases: dict[str, dict[str, str]] | None
) -> LanguageSummary:
    kept, warmup = split_warmup(records)
    turns = [r for r in kept if r.code == OK]
    latencies = [t.latency_ms for t in turns if t.latency_ms is not None]
    wer_value, wer_turns = language_wer(turns, phrases)
    return LanguageSummary(
        lang=lang,
        turns=len(records),
        warmup=warmup,
        ok=len(turns),
        failures=Counter(r.code for r in records if r.code != OK),
        stages=stage_stats(turns),
        latency=stat(latencies, missing=len(turns) - len(latencies)),
        saving=saving_stat(turns),
        wer=wer_value,
        wer_turns=wer_turns,
    )


def summarize(jsonl_text: str, phrases_text: str | None) -> list[LanguageSummary]:
    """One summary per language, sorted by language code."""
    records = parse_records(jsonl_text)
    phrases = None if phrases_text is None else parse_phrases(phrases_text)
    langs = sorted({r.lang for r in records})
    return [
        summarize_language(lang, [r for r in records if r.lang == lang], phrases) for lang in langs
    ]


def fmt_ms(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.0f}" if value == int(value) else f"{value:.1f}"


def stat_row(label: str, item: StageStat, budget: str) -> str:
    cells = [label, str(item.n), str(item.missing), fmt_ms(item.p50), fmt_ms(item.p95), budget]
    return "| " + " | ".join(cells) + " |"


def stage_table(summary: LanguageSummary) -> list[str]:
    lines = [
        "| Stage | n | Missing | p50 ms | p95 ms | Budget ¹ |",
        "|---|---|---|---|---|---|",
        "| Upload tail | - | - | not measured | not measured | part of " + BUDGET["vad"] + " |",
    ]
    for name in STAGES:
        lines.append(stat_row(name, summary.stages[name], BUDGET.get(name, NO_BUDGET)))
    lines.append("| Network + device buffer | - | - | not measured | not measured | 0.3 s |")
    lines.append(
        stat_row("latency_ms (total)", summary.latency, f"p50 target {TARGET_MS / 1000:.0f} s")
    )
    return lines


def target_line(summary: LanguageSummary) -> str:
    p50 = summary.latency.p50
    if p50 is None:
        return f"p50 latency_ms: no ok turns, target {TARGET_MS} ms not checked."
    verdict = "meets" if p50 <= TARGET_MS else "misses"
    return f"p50 latency_ms {fmt_ms(p50)} ms {verdict} the target of {TARGET_MS} ms."


def saving_line(summary: LanguageSummary) -> str:
    gap = summary.saving
    return (
        f"Time per-sentence streaming could save (chat_full - chat_first_sentence): "
        f"p50 {fmt_ms(gap.p50)} ms, p95 {fmt_ms(gap.p95)} ms, n {gap.n}."
    )


def failure_table(summary: LanguageSummary) -> list[str]:
    if not summary.failures:
        return ["Failures: none."]
    lines = ["Failures by code (all turns, warm-up included):", "", "| Code | Count |", "|---|---|"]
    lines += [f"| {code} | {count} |" for code, count in sorted(summary.failures.items())]
    return lines


def wer_line(summary: LanguageSummary) -> str:
    if summary.wer is None:
        return "WER: not computed (no phrase file or no STT text)."
    return f"WER: {summary.wer:.3f} over {summary.wer_turns} ok turns."


def language_section(summary: LanguageSummary) -> list[str]:
    counts = (
        f"Turns: {summary.turns}, warm-up left out: {summary.warmup}, "
        f"ok turns used: {summary.ok}, failed: {sum(summary.failures.values())}."
    )
    return [
        f"## {summary.lang}",
        "",
        counts,
        "",
        *stage_table(summary),
        "",
        target_line(summary),
        "",
        saving_line(summary),
        "",
        *failure_table(summary),
        "",
        wer_line(summary),
        "",
    ]


def render(summaries: Sequence[LanguageSummary]) -> str:
    lines = [
        "# Voice latency report",
        "",
        "Input is synthetic Piper audio, not real speech from the device (F4).",
        f"Percentiles: {METHOD}.",
        "Warm-up: the first record of each run is left out. Failed turns are left out of "
        "the numbers.",
        "",
    ]
    for summary in summaries:
        lines += language_section(summary)
    lines.append(
        "¹ The budget assumes per-sentence streaming (docs/architecture.md). The pipeline "
        "waits for the full chat reply, so chat_first_sentence is passive and "
        "mod_reply covers the whole reply."
    )
    return "\n".join(lines) + "\n"


def build_report(jsonl_text: str, phrases_text: str | None = None) -> str:
    return render(summarize(jsonl_text, phrases_text))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Latency report from a latency run JSONL.")
    parser.add_argument("run", type=Path, help="run.jsonl from ota_server.latency_run")
    parser.add_argument("--phrases", type=Path, help="m3-phrases.json for WER")
    args = parser.parse_args(argv)
    jsonl_text = args.run.read_text(encoding="utf-8")
    phrases_text = None if args.phrases is None else args.phrases.read_text(encoding="utf-8")
    sys.stdout.write(build_report(jsonl_text, phrases_text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

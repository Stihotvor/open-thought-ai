"""Latency run: play the phrase WAV files of one language through VoicePipeline (T5).

Usage (from the repo root, where `.env` is; see docs/guides/latency-run.md):
    python -m ota_server.latency_run --audio <dir> --lang en [--pause 5] [--resume] --out <dir>

Reads <audio>/<lang>/<id>.wav in sorted id order and plays each as one voice turn with real
endpoints from OTA_<ROLE>_* (as the server does), with --pause seconds between turns.
Each turn's `turn.latency` line is captured from the pipeline logger and appended to
<out>/<lang>.log. One run record per turn is appended to <out>/run.jsonl (the "Run record"
in docs/design/voice-latency-report/decision.md), read by ota_server.latency_report.
The STT text goes in run.jsonl only, never in the log (F5).

--resume skips the phrase ids already in run.jsonl for this language; a turn that ended
with `rate_limited` is not done and is played again. A damaged last line (an interrupted
write) is dropped from run.jsonl with a warning.
"""

import argparse
import asyncio
import contextlib
import json
import logging
import secrets
import sys
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .audio import AudioError, wav_to_pcm
from .config import model_env
from .endpoints import ConfigError
from .main import voice_roles
from .pipeline import LANGUAGE_NAMES, VoicePipeline
from .pipeline import log as pipeline_log
from .protocol import TurnError
from .providers import ModelClient, make_http_client
from .timing import TurnTiming, parse_line

log = logging.getLogger(__name__)

RUN_FILE = "run.jsonl"
NOT_DONE = {"rate_limited"}  # codes that --resume plays again

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class RunConfig:
    audio_dir: Path
    lang: str
    out: Path
    pause: float
    resume: bool
    run_id: str


def new_run_id() -> str:
    return f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(2)}"


def _parse_lines(lines: list[str]) -> list[dict[str, Any]]:
    """JSON objects of the lines. Only the last line may be damaged: it is skipped."""
    records: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            if index < len(lines) - 1:
                raise
            log.warning("%s: the damaged last line is skipped", RUN_FILE)
    return records


def read_records(path: Path) -> list[dict[str, Any]]:
    """The run records in path, none if it does not exist."""
    if not path.exists():
        return []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return _parse_lines(lines)


def drop_damaged_tail(path: Path) -> None:
    """Remove a damaged last line, so new records start on a clean line."""
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    body, _, last = text.rstrip("\n").rpartition("\n")
    if not last.strip():
        return
    try:
        json.loads(last)
    except json.JSONDecodeError:
        log.warning("%s: the damaged last line is dropped", RUN_FILE)
        path.write_text(body + "\n" if body else "", encoding="utf-8")
        return
    if not text.endswith("\n"):
        with path.open("a", encoding="utf-8") as f:
            f.write("\n")


def done_ids(records: Sequence[dict[str, Any]], lang: str) -> set[str]:
    return {
        str(r.get("phrase_id"))
        for r in records
        if r.get("lang") == lang and r.get("code") not in NOT_DONE
    }


def phrase_files(audio_dir: Path, lang: str) -> list[Path]:
    files = sorted((audio_dir / lang).glob("*.wav"), key=lambda p: p.stem)
    if not files:
        raise SystemExit(f"no WAV files in {audio_dir / lang}")
    return files


class LineCatcher(logging.Handler):
    """Keeps the `turn.latency` lines logged to the logger it is added to."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if parse_line(message) is not None:
            self.lines.append(message)


@contextlib.contextmanager
def capture(logger: logging.Logger) -> Iterator[LineCatcher]:
    """Catch the logger's info lines for the block, then restore its level."""
    catcher, level = LineCatcher(), logger.level
    if not logger.isEnabledFor(logging.INFO):
        logger.setLevel(logging.INFO)
    logger.addHandler(catcher)
    try:
        yield catcher
    finally:
        logger.removeHandler(catcher)
        logger.setLevel(level)


async def play(pipeline: VoicePipeline, pcm: bytes) -> tuple[str | None, str]:
    """Drain one turn. Return the STT text (from notify) and the turn's code."""
    heard: list[str] = []

    async def notify(text: str) -> None:
        heard.append(text)

    code = "ok"
    try:
        async for _ in pipeline(pcm, (), notify=notify):
            pass
    except TurnError as err:
        code = err.code
    except Exception as exc:  # pylint: disable=broad-exception-caught  # the run goes on
        log.error("turn failed: %s", type(exc).__name__)
        code = "internal"
    return (heard[-1] if heard else None), code


def make_record(
    cfg: RunConfig, phrase_id: str, timing: TurnTiming, stt_text: str | None
) -> dict[str, Any]:
    return {
        "run": cfg.run_id,
        "lang": cfg.lang,
        "phrase_id": phrase_id,
        "turn_id": timing.turn_id,
        "code": timing.code,
        "latency_ms": timing.latency_ms,
        "stages": timing.stages,
        "stt_text": stt_text,
    }


def _append(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


async def run_phrase(
    pipeline: VoicePipeline, catcher: LineCatcher, cfg: RunConfig, wav: Path
) -> dict[str, Any]:
    """Play one WAV file and append its log line and run record."""
    seen = len(catcher.lines)
    try:
        stt_text, code = await play(pipeline, wav_to_pcm(wav.read_bytes()))
    except AudioError as exc:
        raise SystemExit(f"{wav}: {exc}") from None
    new = catcher.lines[seen:]
    timing = parse_line(new[-1]) if new else None
    if timing is None:
        timing = TurnTiming(pipeline.turns, code, None, {})
    else:
        _append(cfg.out / f"{cfg.lang}.log", new[-1])
    record = make_record(cfg, wav.stem, timing, stt_text)
    _append(cfg.out / RUN_FILE, json.dumps(record, ensure_ascii=False))
    log.info("%s %s: %s", cfg.lang, wav.stem, timing.code)
    return record


async def run(
    pipeline: VoicePipeline, cfg: RunConfig, sleep: Sleep = asyncio.sleep
) -> list[dict[str, Any]]:
    """Play the phrases of cfg.lang, skip done ones on --resume. Return the new records."""
    cfg.out.mkdir(parents=True, exist_ok=True)
    path = cfg.out / RUN_FILE
    drop_damaged_tail(path)
    skip = done_ids(read_records(path), cfg.lang) if cfg.resume else set()
    todo = [wav for wav in phrase_files(cfg.audio_dir, cfg.lang) if wav.stem not in skip]
    records: list[dict[str, Any]] = []
    with capture(pipeline_log) as catcher:
        for index, wav in enumerate(todo):
            if index:
                await sleep(cfg.pause)
            records.append(await run_phrase(pipeline, catcher, cfg, wav))
    return records


async def run_from_env(cfg: RunConfig) -> int:
    """Build the pipeline from OTA_<ROLE>_* as the server does, and run."""
    env = model_env()
    try:
        roles = voice_roles(env)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    async with make_http_client() as http:
        pipeline = VoicePipeline(ModelClient(roles, http), cfg.lang, env.get("OTA_TTS_VOICE", ""))
        await run(pipeline, cfg)
    return 0


def parse_args(argv: Sequence[str] | None) -> RunConfig:
    parser = argparse.ArgumentParser(prog="python -m ota_server.latency_run")
    parser.add_argument("--audio", "--audio-dir", dest="audio", type=Path, required=True)
    parser.add_argument("--lang", choices=sorted(LANGUAGE_NAMES), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--pause", type=float, default=5.0, help="seconds between turns")
    parser.add_argument("--resume", action="store_true", help="skip phrase ids already done")
    args = parser.parse_args(argv)
    return RunConfig(args.audio, args.lang, args.out, args.pause, args.resume, new_run_id())


def main(argv: Sequence[str] | None = None) -> int:
    # WARNING for the libraries (httpx logs URLs at INFO); progress lines from this module.
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    log.setLevel(logging.INFO)
    return asyncio.run(run_from_env(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())

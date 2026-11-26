"""Per-turn stage times and the one log line that carries them.

The writer (pipeline) and the reader (latency report) both use this module,
so the line format cannot drift. Line format, all times in whole ms:

    turn.latency id=<int> code=<str> [latency_ms=<int>] [<stage>=<int> ...]

latency_ms and stages that did not run are left out. The line has no text (F5).
Timer calls never raise: a timing bug must not break a turn (F1).
"""

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

STAGES = (
    "vad",
    "stt",
    "chat_first_sentence",
    "chat_full",
    "tts1",
)
PREFIX = "turn.latency"

Clock = Callable[[], float]


class StageTimer:
    """Measures the ms between marks, starting from start()."""

    def __init__(self, clock: Clock = time.perf_counter) -> None:
        self._clock = clock
        self._start: float | None = None
        self._last: float | None = None
        self._stages: dict[str, int] = {}

    def start(self) -> None:
        try:
            self._start = self._last = self._clock()
        except Exception:
            log.exception("timing: start failed")

    def mark(self, stage: str) -> None:
        """Store the ms since the previous mark (or start) under stage."""
        try:
            now = self._clock()
            if self._last is not None:
                self._stages[stage] = round((now - self._last) * 1000)
            self._last = now
        except Exception:
            log.exception("timing: mark %s failed", stage)

    def latency_ms(self) -> int | None:
        """The ms since start(), or None if the timer did not start."""
        try:
            if self._start is None:
                return None
            return round((self._clock() - self._start) * 1000)
        except Exception:
            log.exception("timing: latency failed")
            return None

    @property
    def stages(self) -> dict[str, int]:
        return dict(self._stages)


@dataclass(frozen=True)
class TurnTiming:
    turn_id: int
    code: str
    latency_ms: int | None
    stages: dict[str, int] = field(default_factory=dict)


def format_line(turn_id: int, code: str, latency_ms: int | None, stages: Mapping[str, int]) -> str:
    parts = [PREFIX, f"id={turn_id}", f"code={code}"]
    if latency_ms is not None:
        parts.append(f"latency_ms={latency_ms}")
    parts += [f"{name}={stages[name]}" for name in STAGES if name in stages]
    return " ".join(parts)


def parse_line(line: str) -> TurnTiming | None:
    """Read a format_line line, also inside a log record. Other lines give None."""
    _, sep, tail = line.partition(PREFIX + " ")
    if not sep:
        return None
    try:
        fields = dict(part.split("=", 1) for part in tail.split())
        latency = fields.pop("latency_ms", None)
        return TurnTiming(
            turn_id=int(fields.pop("id")),
            code=fields.pop("code"),
            latency_ms=None if latency is None else int(latency),
            stages={name: int(value) for name, value in fields.items() if name in STAGES},
        )
    except (KeyError, ValueError):
        return None

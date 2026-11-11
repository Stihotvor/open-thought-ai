"""Call limits for model endpoints: token bucket, turn deadline and 429 retry.

Rules (docs/delivery-plan.md, M3):
- Every wait (token, backoff, Retry-After) counts against the turn deadline.
- Retry a 429 at most twice, with 0.5/1/2 s backoff and +-25% jitter, or Retry-After.
- A wait that does not fit in the time left ends the turn at once with RateLimited.
- Cancellation (abort) stops any wait, because all waits are asyncio sleeps.
"""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

BACKOFF = (0.5, 1.0, 2.0)
MAX_RETRIES = 2
JITTER = 0.25
TURN_DEADLINE = 8.0  # seconds, the p95 latency goal
MAX_LLM_CALLS = 4  # per turn (tool loop)
MAX_TTS_CALLS = 20  # per turn

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


class RateLimited(Exception):
    """The provider limit is still hit after the retries, or the wait does not fit the deadline."""


class DeadlineExceeded(Exception):
    """The turn ran out of time."""


class CallCapExceeded(Exception):
    """The turn made too many calls of one kind."""


class Retryable429(Exception):
    """Raised by a call that got HTTP 429 before any byte of the answer arrived."""

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("429")
        self.retry_after = retry_after


@dataclass
class TurnBudget:
    """Time and call limits for one turn."""

    clock: Clock = time.monotonic
    deadline_s: float = TURN_DEADLINE
    max_calls: dict[str, int] = field(
        default_factory=lambda: {"chat": MAX_LLM_CALLS, "tts": MAX_TTS_CALLS}
    )
    calls: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.end = self.clock() + self.deadline_s

    def remaining(self) -> float:
        return self.end - self.clock()

    def count_call(self, role: str) -> None:
        self.calls[role] = self.calls.get(role, 0) + 1
        cap = self.max_calls.get(role)
        if cap is not None and self.calls[role] > cap:
            raise CallCapExceeded(role)


class TokenBucket:
    """Classic token bucket. `rate` tokens per second, at most `burst` tokens."""

    def __init__(self, rate: float, burst: int, clock: Clock = time.monotonic) -> None:
        if rate <= 0 or burst < 1:
            raise ValueError("rate must be > 0 and burst >= 1")
        self.rate = rate
        self.burst = burst
        self.clock = clock
        self.tokens = float(burst)
        self.stamp = clock()

    def _refill(self) -> None:
        now = self.clock()
        self.tokens = min(self.burst, self.tokens + (now - self.stamp) * self.rate)
        self.stamp = now

    def wait_time(self) -> float:
        """Seconds until one token is free. Zero if a token is free now."""
        self._refill()
        return 0.0 if self.tokens >= 1 else (1 - self.tokens) / self.rate

    def take(self) -> None:
        self._refill()
        self.tokens -= 1


async def _wait(seconds: float, budget: TurnBudget, sleep: Sleep) -> None:
    if seconds > budget.remaining():
        raise RateLimited(f"wait {seconds:.2f} s does not fit the turn deadline")
    if seconds > 0:
        await sleep(seconds)


async def limited_call[T](
    call: Callable[[], Awaitable[T]],
    bucket: TokenBucket,
    budget: TurnBudget,
    on_429: Callable[[int], None] = lambda attempt: None,
    sleep: Sleep = asyncio.sleep,
    rand: Callable[[], float] = random.random,
) -> T:
    """Run `call` under the bucket and the turn budget, with the 429 retry rules."""
    for attempt in range(MAX_RETRIES + 1):
        if budget.remaining() <= 0:
            raise DeadlineExceeded()
        await _wait(bucket.wait_time(), budget, sleep)
        bucket.take()
        try:
            return await call()
        except Retryable429 as exc:
            on_429(attempt)
            if attempt == MAX_RETRIES:
                break
            if exc.retry_after is not None:
                delay = exc.retry_after
            else:
                delay = BACKOFF[attempt] * (1 + JITTER * (2 * rand() - 1))
            await _wait(delay, budget, sleep)
    raise RateLimited("429 after retries")

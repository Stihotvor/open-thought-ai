"""Call limits for model endpoints: token bucket, turn budget and 429 retry.

Rules (docs/delivery-plan.md, M3):
- Every wait (token, backoff, Retry-After) counts against the turn deadline.
- Retry a 429 at most twice, with 0.5/1 s backoff and +-25% jitter, or Retry-After.
- A wait that does not fit in the time left ends the turn at once with RateLimited.
- Cancellation (abort) stops any wait, because all waits are asyncio sleeps.

This module knows nothing about roles. The caller passes the call caps.
"""

import asyncio
import math
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

BACKOFF = (0.5, 1.0)  # one entry per retry
MAX_RETRIES = len(BACKOFF)
JITTER = 0.25
TURN_DEADLINE = 8.0  # seconds, the p95 latency goal

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
    """Time and call limits for one turn. A kind without a cap is not counted."""

    max_calls: Mapping[str, int] = field(default_factory=dict)
    clock: Clock = time.monotonic
    deadline_s: float = TURN_DEADLINE
    calls: dict[str, int] = field(default_factory=dict)
    end: float = field(init=False)

    def __post_init__(self) -> None:
        self.end = self.clock() + self.deadline_s

    def remaining(self) -> float:
        """Signed time to the deadline. Negative once the turn is over."""
        return self.end - self.clock()

    def left(self) -> float:
        """Remaining time, never below zero. Use it as a timeout value."""
        return max(self.remaining(), 0.0)

    def count_call(self, kind: str) -> None:
        self.calls[kind] = self.calls.get(kind, 0) + 1
        cap = self.max_calls.get(kind)
        if cap is not None and self.calls[kind] > cap:
            raise CallCapExceeded(kind)


class TokenBucket:
    """Token bucket: `rate` tokens per second, at most `burst` tokens.

    `reserve()` takes a token at once and returns how long the caller must wait for it.
    There is no await between the check and the take, so concurrent tasks cannot overdraw.
    """

    def __init__(self, rate: float, burst: int, clock: Clock = time.monotonic) -> None:
        if not (math.isfinite(rate) and rate > 0) or burst < 1:
            raise ValueError("rate must be finite and > 0, burst >= 1")
        self.rate = rate
        self.burst = burst
        self.clock = clock
        self.tokens = float(burst)
        self.stamp = clock()

    def _refill(self) -> None:
        now = self.clock()
        self.tokens = min(self.burst, self.tokens + (now - self.stamp) * self.rate)
        self.stamp = now

    def reserve(self) -> float:
        self._refill()
        self.tokens -= 1
        return max(0.0, -self.tokens / self.rate)

    def cancel(self) -> None:
        """Give back a reserved token that will not be used."""
        self.tokens = min(self.burst, self.tokens + 1)


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
        wait = bucket.reserve()
        try:
            await _wait(wait, budget, sleep)
        except BaseException:  # no fit, or abort: the token was not used
            bucket.cancel()
            raise
        try:
            return await call()
        except Retryable429 as exc:
            on_429(attempt)
            if attempt == MAX_RETRIES:
                raise RateLimited("429 after retries") from None
            if exc.retry_after is not None:
                delay = exc.retry_after
            else:
                delay = BACKOFF[attempt] * (1 + JITTER * (2 * rand() - 1))
            await _wait(delay, budget, sleep)
    raise AssertionError("unreachable")

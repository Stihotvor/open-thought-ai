import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

import pytest

from ota_server.ratelimit import (
    CallCapExceeded,
    DeadlineExceeded,
    RateLimited,
    Retryable429,
    TokenBucket,
    TurnBudget,
    limited_call,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def make(
    rate: float = 1.0, burst: int = 1, deadline: float = 8.0
) -> tuple[FakeClock, TokenBucket, TurnBudget]:
    clock = FakeClock()
    return clock, TokenBucket(rate, burst, clock), TurnBudget(clock=clock, deadline_s=deadline)


def answers(*results: object) -> tuple[Callable[[], Awaitable[object]], list[int]]:
    """A fake call: each item is a value to return or an exception to raise."""
    items = list(results)
    calls: list[int] = []

    async def call() -> object:
        calls.append(1)
        item = items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return call, calls


def test_bucket_reserve_returns_wait() -> None:
    clock, bucket, _ = make(rate=2.0, burst=1)
    assert bucket.reserve() == 0
    assert bucket.reserve() == pytest.approx(0.5)
    clock.now += 1.0
    assert bucket.reserve() == 0


@pytest.mark.parametrize("rate", [0, -1, float("nan"), float("inf")])
def test_bucket_rejects_bad_rate(rate: float) -> None:
    with pytest.raises(ValueError):
        TokenBucket(rate, 1)


def test_concurrent_callers_do_not_overdraw() -> None:
    clock, bucket, budget = make(rate=1.0, burst=1)
    starts: list[float] = []

    async def call() -> str:
        starts.append(clock.now)
        return "ok"

    async def scenario() -> None:
        await asyncio.gather(
            *(limited_call(call, bucket, budget, sleep=clock.sleep) for _ in range(3))
        )

    run(scenario())
    assert len(starts) == 3
    assert starts[0] == 0 and starts[1] > 0 and starts[2] > starts[1]  # spaced, not a burst


def test_success_without_retry() -> None:
    clock, bucket, budget = make()
    call, calls = answers("ok")
    assert run(limited_call(call, bucket, budget, sleep=clock.sleep)) == "ok"
    assert len(calls) == 1


def test_429_backoff_then_success() -> None:
    clock, bucket, budget = make(rate=10.0)
    call, calls = answers(Retryable429(), "ok")
    result = run(limited_call(call, bucket, budget, sleep=clock.sleep, rand=lambda: 0.5))
    assert result == "ok"
    assert len(calls) == 2
    assert clock.now == pytest.approx(0.5)  # first backoff, no jitter at rand=0.5


@pytest.mark.parametrize(("r", "expected"), [(0.0, 0.375), (1.0, 0.625)])
def test_jitter_stays_within_25_percent(r: float, expected: float) -> None:
    clock, bucket, budget = make(rate=10.0)
    call, _ = answers(Retryable429(), "ok")
    run(limited_call(call, bucket, budget, sleep=clock.sleep, rand=lambda: r))
    assert clock.now == pytest.approx(expected)


def test_two_retries_then_rate_limited() -> None:
    clock, bucket, budget = make(rate=10.0)
    call, calls = answers(Retryable429(), Retryable429(), Retryable429())
    seen: list[int] = []
    with pytest.raises(RateLimited):
        run(
            limited_call(
                call, bucket, budget, on_429=seen.append, sleep=clock.sleep, rand=lambda: 0.5
            )
        )
    assert len(calls) == 3
    assert seen == [0, 1, 2]


def test_retry_after_longer_than_deadline_fails_at_once() -> None:
    clock, bucket, budget = make(deadline=8.0)
    call, calls = answers(Retryable429(retry_after=30), "ok")
    with pytest.raises(RateLimited):
        run(limited_call(call, bucket, budget, sleep=clock.sleep))
    assert len(calls) == 1
    assert clock.now == 0


def test_token_wait_longer_than_deadline_fails_and_gives_token_back() -> None:
    clock, bucket, budget = make(rate=0.1, burst=1, deadline=8.0)
    bucket.reserve()  # next token in 10 s, more than the deadline
    call, calls = answers("ok")
    with pytest.raises(RateLimited):
        run(limited_call(call, bucket, budget, sleep=clock.sleep))
    assert calls == []
    assert bucket.tokens == pytest.approx(0)


def test_expired_budget_raises_deadline() -> None:
    clock, bucket, budget = make(deadline=1.0)
    clock.now = 2.0
    call, calls = answers("ok")
    with pytest.raises(DeadlineExceeded):
        run(limited_call(call, bucket, budget, sleep=clock.sleep))
    assert calls == []


def test_other_errors_are_not_retried() -> None:
    clock, bucket, budget = make()
    call, calls = answers(ValueError("boom"))
    with pytest.raises(ValueError):
        run(limited_call(call, bucket, budget, sleep=clock.sleep))
    assert len(calls) == 1


def test_abort_cancels_backoff() -> None:
    async def scenario() -> list[int]:
        bucket = TokenBucket(10.0, 1)
        budget = TurnBudget()
        call, calls = answers(Retryable429(retry_after=5), "ok")
        task = asyncio.create_task(limited_call(call, bucket, budget))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return calls

    assert len(run(scenario())) == 1


def test_abort_during_token_wait_gives_token_back() -> None:
    async def scenario() -> tuple[TokenBucket, list[int]]:
        bucket = TokenBucket(1.0, 1)
        bucket.reserve()  # next token in 1 s
        call, calls = answers("ok")
        task = asyncio.create_task(limited_call(call, bucket, TurnBudget()))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return bucket, calls

    bucket, calls = run(scenario())
    assert calls == []
    # full bucket (1) - first reserve (1) - aborted reserve (1) + give back (1) + 0.05 s refill
    assert bucket.tokens == pytest.approx(0.05, abs=0.05)


def test_cancel_never_exceeds_burst() -> None:
    _, bucket, _ = make(burst=2)
    bucket.cancel()
    assert bucket.tokens == 2


def test_call_caps_per_turn() -> None:
    budget = TurnBudget(max_calls={"a": 2})
    budget.count_call("a")
    budget.count_call("a")
    with pytest.raises(CallCapExceeded):
        budget.count_call("a")
    budget.count_call("b")  # no cap for this kind


def test_extend_moves_the_deadline_later() -> None:
    now = [0.0]
    budget = TurnBudget(clock=lambda: now[0], deadline_s=8.0)
    now[0] = 10.0
    assert budget.left() == 0.0
    budget.extend(5.0)  # time spent waiting for the device does not count
    assert budget.left() == 3.0

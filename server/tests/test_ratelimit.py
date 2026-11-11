import asyncio

import pytest

from ota_server.ratelimit import (
    CallCapExceeded,
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


def run(coro):
    return asyncio.run(coro)


def make(rate=1.0, burst=1, deadline=8.0):
    clock = FakeClock()
    return clock, TokenBucket(rate, burst, clock), TurnBudget(clock=clock, deadline_s=deadline)


def answers(*results):
    """A fake call: each item is a value to return or an exception to raise."""
    items = list(results)
    calls = []

    async def call():
        calls.append(1)
        item = items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return call, calls


def test_bucket_waits_for_refill():
    clock, bucket, _ = make(rate=2.0, burst=1)
    assert bucket.wait_time() == 0
    bucket.take()
    assert bucket.wait_time() == pytest.approx(0.5)
    clock.now += 0.5
    assert bucket.wait_time() == 0


def test_success_without_retry():
    clock, bucket, budget = make()
    call, calls = answers("ok")
    assert run(limited_call(call, bucket, budget, sleep=clock.sleep)) == "ok"
    assert len(calls) == 1


def test_429_backoff_then_success():
    clock, bucket, budget = make(rate=10.0)
    call, calls = answers(Retryable429(), "ok")
    result = run(limited_call(call, bucket, budget, sleep=clock.sleep, rand=lambda: 0.5))
    assert result == "ok"
    assert len(calls) == 2
    assert clock.now == pytest.approx(0.5)  # first backoff, no jitter at rand=0.5


def test_jitter_stays_within_25_percent():
    for r, expected in ((0.0, 0.375), (1.0, 0.625)):
        clock, bucket, budget = make(rate=10.0)
        call, _ = answers(Retryable429(), "ok")
        run(limited_call(call, bucket, budget, sleep=clock.sleep, rand=lambda r=r: r))
        assert clock.now == pytest.approx(expected)


def test_two_retries_then_rate_limited():
    clock, bucket, budget = make(rate=10.0)
    call, calls = answers(Retryable429(), Retryable429(), Retryable429())
    seen = []
    with pytest.raises(RateLimited):
        run(limited_call(call, bucket, budget, seen.append, clock.sleep, lambda: 0.5))
    assert len(calls) == 3
    assert seen == [0, 1, 2]


def test_retry_after_longer_than_deadline_fails_at_once():
    clock, bucket, budget = make(deadline=8.0)
    call, calls = answers(Retryable429(retry_after=30), "ok")
    with pytest.raises(RateLimited):
        run(limited_call(call, bucket, budget, sleep=clock.sleep))
    assert len(calls) == 1
    assert clock.now == 0


def test_token_wait_counts_against_deadline():
    clock, bucket, budget = make(rate=0.1, burst=1, deadline=8.0)
    bucket.take()  # next token in 10 s, more than the deadline
    call, calls = answers("ok")
    with pytest.raises(RateLimited):
        run(limited_call(call, bucket, budget, sleep=clock.sleep))
    assert calls == []


def test_other_errors_are_not_retried():
    clock, bucket, budget = make()
    call, calls = answers(ValueError("boom"))
    with pytest.raises(ValueError):
        run(limited_call(call, bucket, budget, sleep=clock.sleep))
    assert len(calls) == 1


def test_abort_cancels_backoff():
    async def scenario():
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


def test_call_caps_per_turn():
    budget = TurnBudget(max_calls={"chat": 2})
    budget.count_call("chat")
    budget.count_call("chat")
    with pytest.raises(CallCapExceeded):
        budget.count_call("chat")
    budget.count_call("stt")  # no cap for this role

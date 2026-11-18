"""PendingCalls: tool.call ids, matching tool.result, timeout and close (decision D6)."""

import asyncio
import logging
from typing import Any

import pytest

from ota_server.hub.pending import DeviceResult, PendingCalls, PendingClosed


async def no_send() -> None:
    pass


async def answer(pending: PendingCalls, call_id: int, msg: dict[str, Any]) -> DeviceResult:
    """Wait for call_id, then feed `msg` as its tool.result."""
    waiter = asyncio.create_task(pending.wait_for(call_id, 1.0, no_send))
    while len(pending) == 0:
        await asyncio.sleep(0)
    assert pending.resolve({"type": "tool.result", "id": call_id, **msg})
    return await waiter


@pytest.mark.parametrize(
    ("msg", "expected"),
    [
        ({"ok": True, "result": "30"}, DeviceResult(True, "30")),
        ({"ok": True, "result": {"volume": 30}}, DeviceResult(True, '{"volume": 30}')),
        ({"ok": True}, DeviceResult(True, "ok")),
        ({"ok": False, "error": "busy"}, DeviceResult(False, "busy")),
        ({"ok": "yes"}, DeviceResult(False, "malformed tool.result")),
        ({"ok": False}, DeviceResult(False, "malformed tool.result")),
        ({"ok": False, "error": 5}, DeviceResult(False, "malformed tool.result")),
    ],
)
def test_result_shapes(msg: dict[str, Any], expected: DeviceResult) -> None:
    pending = PendingCalls()
    assert asyncio.run(answer(pending, 1, msg)) == expected
    assert len(pending) == 0


@pytest.mark.parametrize("call_id", [99, "1", True, None])
def test_unknown_id_is_ignored_and_logged(
    call_id: object, caplog: pytest.LogCaptureFixture
) -> None:
    pending = PendingCalls()
    with caplog.at_level(logging.INFO):
        assert not pending.resolve({"type": "tool.result", "id": call_id, "ok": True})
    assert "tool.result" in caplog.text


def test_an_answered_id_is_unknown() -> None:
    pending = PendingCalls()
    asyncio.run(answer(pending, 1, {"ok": True, "result": "30"}))
    assert not pending.resolve({"type": "tool.result", "id": 1, "ok": True})


def test_ids_are_unique_and_start_at_1() -> None:
    pending = PendingCalls()
    first = [pending.new_id() for _ in range(3)]
    second = [pending.new_id() for _ in range(3)]  # the next turn, same session
    assert first[0] == 1
    assert len(set(first + second)) == 6


def test_timeout_leaves_no_entry() -> None:
    pending = PendingCalls()
    with pytest.raises(TimeoutError):
        asyncio.run(pending.wait_for(1, 0.05, no_send))
    assert len(pending) == 0


def test_a_failed_send_leaves_no_entry() -> None:
    async def broken() -> None:
        raise ConnectionError("socket closed")

    pending = PendingCalls()
    with pytest.raises(ConnectionError):
        asyncio.run(pending.wait_for(1, 1.0, broken))
    assert len(pending) == 0


def test_fail_all_raises_pending_closed() -> None:
    async def scenario() -> list[BaseException | DeviceResult]:
        pending = PendingCalls()
        waits = [asyncio.create_task(pending.wait_for(i, 5.0, no_send)) for i in (1, 2)]
        while len(pending) < 2:
            await asyncio.sleep(0)
        pending.fail_all()
        results = await asyncio.gather(*waits, return_exceptions=True)
        assert len(pending) == 0
        return results

    assert all(isinstance(r, PendingClosed) for r in asyncio.run(scenario()))

"""Tool calls that wait for the device's tool.result, matched by id (decision D6)."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

MALFORMED = "malformed tool.result"


@dataclass(frozen=True)
class DeviceResult:
    ok: bool
    text: str  # the result as text, or the error text


class PendingClosed(Exception):
    """The session closed while the call waited for its result."""


def parse_result(msg: dict[str, Any]) -> DeviceResult:
    """The device's tool.result as a DeviceResult. A bad shape is an error result (E4)."""
    ok = msg.get("ok")
    if not isinstance(ok, bool):
        return DeviceResult(False, MALFORMED)
    if ok:
        if "result" not in msg:
            return DeviceResult(True, "ok")
        result = msg["result"]
        return DeviceResult(True, result if isinstance(result, str) else json.dumps(result))
    error = msg.get("error")
    if not isinstance(error, str):
        return DeviceResult(False, MALFORMED)
    return DeviceResult(False, error)


class PendingCalls:
    """The calls of one session that wait for a tool.result. Ids are never reused."""

    def __init__(self) -> None:
        self._last_id = 0
        self._waiting: dict[int, asyncio.Future[DeviceResult]] = {}

    def __len__(self) -> int:
        return len(self._waiting)

    def new_id(self) -> int:
        self._last_id += 1
        return self._last_id

    async def wait_for(
        self, call_id: int, timeout: float, send: Callable[[], Awaitable[None]]
    ) -> DeviceResult:
        """Register call_id, run send(), then wait for the result; send and wait share one
        timeout. Raises TimeoutError on expiry and PendingClosed after fail_all()."""
        future: asyncio.Future[DeviceResult] = asyncio.get_running_loop().create_future()
        self._waiting[call_id] = future
        try:
            async with asyncio.timeout(timeout):
                await send()
                return await future
        finally:
            self._waiting.pop(call_id, None)  # also on cancel or a failed send
            if future.done() and not future.cancelled():
                future.exception()  # mark it seen: no "exception never retrieved" warning

    def resolve(self, msg: dict[str, Any]) -> bool:
        """Give a tool.result to its waiting call. Unknown ids are logged and ignored (E3)."""
        call_id = msg.get("id")
        future = None
        if isinstance(call_id, int) and not isinstance(call_id, bool):
            future = self._waiting.pop(call_id, None)
        if future is None or future.done():  # done: cancelled, its finally not run yet
            log.info("ignored tool.result for unknown id %r", call_id)
            return False
        future.set_result(parse_result(msg))
        return True

    def fail_all(self) -> None:
        """End every waiting call with PendingClosed: the socket closed."""
        waiting, self._waiting = self._waiting, {}
        for future in waiting.values():
            if not future.done():
                future.set_exception(PendingClosed())

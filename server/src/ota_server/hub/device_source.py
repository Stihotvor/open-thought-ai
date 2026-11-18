"""The device tool source: three fixed tools sent as tool.call frames (decisions D2, D4)."""

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ota_server.hub.pending import DeviceResult, PendingCalls, PendingClosed
from ota_server.hub.types import ToolOutcome, ToolSpec
from ota_server.protocol import MAX_TEXT_FRAME

log = logging.getLogger(__name__)

DEVICE_TIMEOUT = 2.0
FACES = ("idle", "listening", "thinking", "speaking", "offline", "error")  # firmware/main/face.h

Send = Callable[[dict[str, Any]], Awaitable[None]]


def _specs() -> list[ToolSpec]:
    volume = {
        "type": "object",
        "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 100}},
        "required": ["level"],
        "additionalProperties": False,
    }
    face = {
        "type": "object",
        "properties": {"face": {"type": "string", "enum": list(FACES)}},
        "required": ["face"],
        "additionalProperties": False,
    }
    status = {"type": "object", "properties": {}, "additionalProperties": False}
    return [
        ToolSpec("device", "set_volume", "Set the speaker volume, 0 to 100.", volume),
        ToolSpec("device", "set_face", "Show a face on the device screen.", face),
        ToolSpec("device", "get_status", "Read the device status.", status),
    ]


def _from_result(result: DeviceResult) -> ToolOutcome:
    if result.ok:
        return ToolOutcome(True, result.text)
    return ToolOutcome(False, result.text, "device_error")


class DeviceSource:
    """ToolSource for the connected device; call() never raises except on cancel."""

    name = "device"

    def __init__(self, send: Send, pending: PendingCalls, timeout: float = DEVICE_TIMEOUT) -> None:
        self._send = send
        self._pending = pending
        self.timeout = timeout

    def specs(self) -> list[ToolSpec]:
        return _specs()

    async def call(self, tool: str, args: dict[str, Any]) -> ToolOutcome:
        call_id = self._pending.new_id()
        frame = {"type": "tool.call", "id": call_id, "name": tool, "args": args}
        if len(json.dumps(frame, separators=(",", ":")).encode()) > MAX_TEXT_FRAME:
            return ToolOutcome(False, "tool call too large", "frame_too_large")
        try:
            result = await self._pending.wait_for(call_id, self.timeout, lambda: self._send(frame))
        except TimeoutError:
            log.warning(
                "device tool %s (id %d) timed out after %.1f s", tool, call_id, self.timeout
            )
            return ToolOutcome(False, "tool_timeout", "tool_timeout")
        except PendingClosed:
            return ToolOutcome(False, "device disconnected", "device_closed")
        except Exception:
            log.warning("could not send tool call %s (id %d)", tool, call_id, exc_info=True)
            return ToolOutcome(False, "could not send tool call", "send_failed")
        return _from_result(result)

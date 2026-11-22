"""T3: the confirm flags of the device tools (D10)."""

from typing import Any

from ota_server.hub.device_source import DeviceSource
from ota_server.hub.pending import PendingCalls


async def _send(frame: dict[str, Any]) -> None:
    return None


def test_device_confirm_flags() -> None:
    specs = DeviceSource(_send, PendingCalls()).specs()
    flags = {spec.name: spec.confirm for spec in specs}
    assert flags == {"set_volume": True, "set_face": False, "get_status": False}

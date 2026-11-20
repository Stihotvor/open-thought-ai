"""HashToolFilter (D3): admit a tool only when its hash matches the owner's pin.

A changed or hostile server cannot change what the LLM sees without owner approval (B1).
The owner copies the current hash from the WARNING log into the servers file.
"""

import hashlib
import json
import logging
from collections.abc import Mapping
from typing import Any

from ota_server.mcp.port import RawTool

log = logging.getLogger(__name__)

MAX_DESCRIPTION = 1000
MAX_SCHEMA = 4000


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical(tool: RawTool) -> str:
    """The canonical JSON of the bare name, description and input schema."""
    return _dumps({"name": tool.name, "description": tool.description, "input_schema": tool.schema})


def schema_json(tool: RawTool) -> str:
    """The canonical JSON of the input schema only."""
    return _dumps(tool.schema)


def tool_hash(tool: RawTool) -> str:
    """Return "sha256:" + hex SHA-256 of the UTF-8 canonical JSON."""
    return "sha256:" + hashlib.sha256(canonical(tool).encode("utf-8")).hexdigest()


class HashToolFilter:
    """A ToolFilter: caps first (T4), then the pin compare (T3, E4)."""

    def __init__(self, pins: Mapping[str, Mapping[str, str]]) -> None:
        self._pins = pins

    def admit(self, server: str, tool: RawTool) -> bool:
        if len(tool.description) > MAX_DESCRIPTION or len(schema_json(tool)) > MAX_SCHEMA:
            log.error(
                "mcp %s: tool %r not admitted, description or schema too long", server, tool.name
            )
            return False
        current = tool_hash(tool)
        if self._pins.get(server, {}).get(tool.name) == current:
            return True
        # Never log the description: it is server-controlled text.
        log.warning("mcp %s: tool %r not admitted, current hash %s", server, tool.name, current)
        return False

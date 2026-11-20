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


def _canonical_with(tool: RawTool, schema_text: str) -> str:
    """Build the canonical JSON around an already serialized schema (keys in sorted order)."""
    return (
        f'{{"description":{_dumps(tool.description)},'
        f'"input_schema":{schema_text},"name":{_dumps(tool.name)}}}'
    )


def canonical(tool: RawTool) -> str:
    """The canonical JSON of the bare name, description and input schema."""
    return _canonical_with(tool, schema_json(tool))


def schema_json(tool: RawTool) -> str:
    """The canonical JSON of the input schema only."""
    return _dumps(tool.schema)


def _hash_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def tool_hash(tool: RawTool) -> str:
    """Return "sha256:" + hex SHA-256 of the UTF-8 canonical JSON."""
    return _hash_text(canonical(tool))


class HashToolFilter:
    """A ToolFilter: caps first (T4), then the pin compare (T3, E4)."""

    def __init__(self, pins: Mapping[str, Mapping[str, str]]) -> None:
        self._pins = pins

    def admit(self, server: str, tool: RawTool) -> bool:
        schema_text = schema_json(tool)
        if len(tool.description) > MAX_DESCRIPTION or len(schema_text) > MAX_SCHEMA:
            log.error(
                "mcp %s: tool %r not admitted, description or schema too long", server, tool.name
            )
            return False
        current = _hash_text(_canonical_with(tool, schema_text))
        if self._pins.get(server, {}).get(tool.name) == current:
            return True
        # Never log the description: it is server-controlled text.
        log.warning("mcp %s: tool %r not admitted, current hash %s", server, tool.name, current)
        return False

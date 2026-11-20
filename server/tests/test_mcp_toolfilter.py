"""T3: HashToolFilter, tool_hash and the size caps (D3, T3, T4, E4, M2)."""

import hashlib
import json
import logging

import pytest

from ota_server.mcp.port import RawTool
from ota_server.mcp.toolfilter import (
    MAX_DESCRIPTION,
    MAX_SCHEMA,
    HashToolFilter,
    canonical,
    schema_json,
    tool_hash,
)

LOGGER = "ota_server.mcp.toolfilter"
WEATHER = RawTool(
    name="get_weather",
    description="Weather for a city",
    schema={
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
)
WEATHER_HASH = "sha256:032559f272a9c312fecbc4e9046742677097b754116ba5928f4cfc2e580bb716"


def _tool(description: str = "a tool", schema: dict[str, object] | None = None) -> RawTool:
    return RawTool(name="t", description=description, schema=schema or {"type": "object"})


def _pinned(tool: RawTool) -> HashToolFilter:
    return HashToolFilter({"s": {"t": tool_hash(tool)}})


def _records(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == level]


def test_matching_pin_is_admitted() -> None:
    tool = _tool()
    assert _pinned(tool).admit("s", tool) is True


def test_missing_pin_is_not_admitted(caplog: pytest.LogCaptureFixture) -> None:
    tool = _tool(description="secret words")
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert HashToolFilter({}).admit("s", tool) is False
    warnings = _records(caplog, logging.WARNING)
    assert any("'t'" in m and tool_hash(tool) in m and "s" in m for m in warnings)
    assert all("secret words" not in r.getMessage() for r in caplog.records)


def test_changed_description_is_not_admitted(caplog: pytest.LogCaptureFixture) -> None:
    old = _tool(description="old text")
    new = _tool(description="new text")
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert _pinned(old).admit("s", new) is False
    assert any(tool_hash(new) in m for m in _records(caplog, logging.WARNING))


def test_description_over_cap(caplog: pytest.LogCaptureFixture) -> None:
    tool = _tool(description="x" * (MAX_DESCRIPTION + 1))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert _pinned(tool).admit("s", tool) is False
    errors = _records(caplog, logging.ERROR)
    assert len(errors) == 1 and "'t'" in errors[0] and "s" in errors[0]
    assert all("x" * 50 not in r.getMessage() for r in caplog.records)


def test_description_at_cap_is_admitted() -> None:
    assert MAX_DESCRIPTION == 1000
    tool = _tool(description="x" * 1000)
    assert _pinned(tool).admit("s", tool) is True


def test_schema_over_cap(caplog: pytest.LogCaptureFixture) -> None:
    tool = _tool(schema={"type": "object", "description": "y" * MAX_SCHEMA})
    assert MAX_SCHEMA == 4000 and len(schema_json(tool)) > 4000
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert _pinned(tool).admit("s", tool) is False
    errors = _records(caplog, logging.ERROR)
    assert len(errors) == 1 and "'t'" in errors[0]


def test_stable_hash() -> None:
    reordered = RawTool(
        name=WEATHER.name,
        description=WEATHER.description,
        schema={
            "required": ["city"],
            "properties": {"city": {"type": "string"}},
            "type": "object",
        },
    )
    assert tool_hash(WEATHER) == WEATHER_HASH
    assert tool_hash(reordered) == WEATHER_HASH


def test_non_ascii_is_hashed_as_utf8() -> None:
    a = RawTool(name="t", description="café", schema={})
    b = RawTool(name="t", description="cafe", schema={})
    assert tool_hash(a) != tool_hash(b)
    assert tool_hash(a).startswith("sha256:") and len(tool_hash(a)) == 7 + 64


def test_canonical_matches_one_json_dump() -> None:
    """The canonical JSON built around the schema text equals one sorted dump of the tool."""
    tool = RawTool(name='t"x', description='café "q"', schema={"b": [1, {"z": "é"}], "a": None})
    full = {"name": tool.name, "description": tool.description, "input_schema": tool.schema}
    text = json.dumps(full, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert canonical(tool) == text
    assert tool_hash(tool) == "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()

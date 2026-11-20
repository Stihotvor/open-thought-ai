"""QA cases for the tool filter, hub admission and denied servers (no network)."""

import asyncio
import logging
import re
from contextlib import AsyncExitStack
from typing import Any

import pytest
from qa_support import (
    EMPTY,
    PIN0,
    FakeConn,
    FakeResolver,
    as_resolver,
    raw,
    remote,
    run_tool,
)

from ota_server.hub.hub import McpHub
from ota_server.hub.types import ToolOutcome
from ota_server.mcp.config import McpServerConfig
from ota_server.mcp.policy import SafeConnectionPolicy
from ota_server.mcp.port import RawTool
from ota_server.mcp.registry import McpRegistry
from ota_server.mcp.source import McpSource
from ota_server.mcp.toolfilter import MAX_SCHEMA, HashToolFilter, schema_json, tool_hash

HASH = re.compile(r"sha256:[0-9a-f]{64}")


def build(
    tools: list[RawTool], pins: dict[str, str], name: str = "alpha", tool_filter: Any = None
) -> McpSource:
    config = McpServerConfig(name, "http", url="https://x.example/", tools=pins)
    return McpSource(config, FakeConn(tools), tools, tool_filter or HashToolFilter({name: pins}))


def names(source: McpSource) -> list[str]:
    return [s.full_name for s in source.specs()]


def test_qa_061_hash_from_the_log_approves_the_tool(caplog: pytest.LogCaptureFixture) -> None:
    tool = raw()
    with caplog.at_level(logging.WARNING):
        assert names(build([tool], {"echo": PIN0})) == []  # a stale pin logs the current hash
    found = HASH.search(caplog.text)
    assert found is not None
    assert names(build([tool], {"echo": found.group(0)})) == ["alpha__echo"]


def test_qa_063_changed_input_schema_is_not_admitted() -> None:
    old = raw()
    schema = {"type": "object", "properties": {"text": {}, "target": {}}, "required": ["target"]}
    new = raw(schema=schema)
    assert names(build([new], {"echo": tool_hash(old)})) == []


def test_qa_065_hash_covers_the_bare_name_only() -> None:
    pin = {"echo": tool_hash(raw())}
    assert names(build([raw()], pin, "a")) == ["a__echo"]
    assert names(build([raw()], pin, "b")) == ["b__echo"]


def test_qa_069_schema_of_exactly_4000_chars_is_admitted() -> None:
    pad = MAX_SCHEMA - len(schema_json(raw(schema={"type": "object", "description": ""})))
    tool = raw(schema={"type": "object", "description": "y" * pad})
    assert len(schema_json(tool)) == MAX_SCHEMA
    assert names(build([tool], {"echo": tool_hash(tool)})) == ["alpha__echo"]


def test_qa_070_cap_error_without_pin_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Actual behavior: only the cap ERROR, no missing-pin WARNING. The docs leave it open."""
    tool = raw(description="x" * 1001)
    with caplog.at_level(logging.DEBUG):
        assert HashToolFilter({}).admit("alpha", tool) is False
    levels = [r.levelno for r in caplog.records]
    assert logging.ERROR in levels
    assert logging.WARNING not in levels


def test_qa_071_listed_tool_absent_on_the_server(caplog: pytest.LogCaptureFixture) -> None:
    tool = raw()
    with caplog.at_level(logging.WARNING):
        source = build([tool], {"ghost": PIN0, "echo": tool_hash(tool)})
    assert names(source) == ["alpha__echo"]
    assert any("ghost" in r.getMessage() for r in caplog.records)


def test_qa_073_unadmitted_call_is_the_unknown_tool_outcome() -> None:
    hub = McpHub([build([raw()], {})])
    real = asyncio.run(run_tool(hub, "alpha__echo", {"text": "x"}))
    never = asyncio.run(run_tool(hub, "alpha__never_existed", {"text": "x"}))
    assert real == never
    assert (real.ok, real.code) == (False, "unknown_tool")


def test_qa_074_unadmitted_tool_is_not_in_the_llm_list() -> None:
    echo, danger = raw(), raw("danger", "Deletes things")
    source = build([echo, danger], {"echo": tool_hash(echo)})
    listed = [t["function"]["name"] for t in McpHub([source]).list_tools()]
    assert listed == ["alpha__echo"]


class Raising:
    def admit(self, server: str, tool: RawTool) -> bool:
        if tool.name == "danger":
            raise RuntimeError("boom secret-text")
        return True


def test_qa_075_filter_exception_hides_only_that_tool(caplog: pytest.LogCaptureFixture) -> None:
    tools = [raw(), raw("danger")]
    with caplog.at_level(logging.ERROR):
        source = build(tools, {"echo": PIN0, "danger": PIN0}, tool_filter=Raising())
    assert names(source) == ["alpha__echo"]
    assert any(r.levelno == logging.ERROR and "danger" in r.getMessage() for r in caplog.records)
    assert "secret-text" not in caplog.text


def test_qa_078_denied_server_call_is_an_unknown_tool_outcome() -> None:
    async def run() -> ToolOutcome:
        resolver = as_resolver(FakeResolver())
        policy = SafeConnectionPolicy(EMPTY, resolver, lambda: {})
        bad = remote("https://169.254.169.254/mcp", name="bad")
        bad = McpServerConfig("bad", "http", url=bad.url, tools={"echo": PIN0})
        async with AsyncExitStack() as stack:
            sources = await McpRegistry.start([bad], stack, policy, HashToolFilter({"bad": {}}))
            assert sources == []
            return await run_tool(McpHub(sources), "bad__echo", {"text": "x"})

    outcome = asyncio.run(run())
    assert (outcome.ok, outcome.code) == (False, "unknown_tool")

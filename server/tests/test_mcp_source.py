"""T2: McpSource policy over a FakeConnection with no SDK (D5, D5a, D6)."""

import asyncio
import inspect
import logging
import time
from typing import Any

import pytest

from ota_server.hub import hub, types
from ota_server.hub.types import ToolOutcome
from ota_server.mcp import hooks
from ota_server.mcp.config import McpServerConfig
from ota_server.mcp.port import RawResult, RawTool
from ota_server.mcp.registry import McpRegistry
from ota_server.mcp.source import McpSource

PIN = "sha256:" + "0" * 64
OK = RawResult(is_error=False, parts=[("text", "done")])


class FakeConnection:
    """An McpConnection with a controllable delay, exception and result."""

    def __init__(
        self,
        tools: list[str],
        result: RawResult = OK,
        delay: float = 0.0,
        raises: Exception | None = None,
    ) -> None:
        self.tools = [RawTool(name=t, description=f"tool {t}", schema={}) for t in tools]
        self.result = result
        self.delay = delay
        self.raises = raises
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[RawTool]:
        return list(self.tools)

    async def call_tool(self, name: str, args: dict[str, Any]) -> RawResult:
        self.calls.append((name, args))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises is not None:
            raise self.raises
        return self.result

    async def close(self) -> None:
        return None


class DenyFilter:
    def __init__(self, denied: str) -> None:
        self.denied = denied

    def admit(self, server: str, tool: RawTool) -> bool:
        return tool.name != self.denied


def _config(tools: list[str], timeout: float = 10.0) -> McpServerConfig:
    pins = dict.fromkeys(tools, PIN)
    return McpServerConfig(
        name="srv", transport="http", url="https://h/mcp", timeout=timeout, tools=pins
    )


def _build(conn: FakeConnection, tools: list[str], **kw: Any) -> McpSource:
    timeout = kw.pop("timeout", 10.0)
    return asyncio.run(McpSource.build(_config(tools, timeout), conn, **kw))


def _names(source: McpSource) -> list[str]:
    return [spec.full_name for spec in source.specs()]


def test_unlisted_tool_is_hidden() -> None:
    conn = FakeConnection(["a", "b"])
    source = _build(conn, ["a"])
    assert _names(source) == ["srv__a"]
    assert source.specs()[0].source == "srv"
    assert source.name == "srv" and source.timeout == 10.0
    out = asyncio.run(source.call("b", {}))
    assert out.code == "unknown_tool" and not out.ok
    assert conn.calls == []


def test_allowlist_name_missing_on_server(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="ota_server.mcp.source"):
        source = _build(FakeConnection(["a"]), ["a", "gone"])
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("gone" in r.getMessage() for r in warnings)
    assert _names(source) == ["srv__a"]


def test_bad_full_name_is_dropped(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="ota_server.mcp.source"):
        source = _build(FakeConnection(["bad name", "a"]), ["bad name", "a"])
    assert _names(source) == ["srv__a"]
    assert any(r.levelno == logging.ERROR and "bad name" in r.getMessage() for r in caplog.records)


def test_tool_filter_hides_a_tool() -> None:
    source = _build(FakeConnection(["a", "b"]), ["a", "b"], tool_filter=DenyFilter("a"))
    assert _names(source) == ["srv__b"]


def test_is_error_gives_generic_text() -> None:
    result = RawResult(is_error=True, parts=[("text", "secret stack trace")])
    source = _build(FakeConnection(["a"], result=result), ["a"])
    out = asyncio.run(source.call("a", {}))
    assert out == ToolOutcome(False, "the tool reported an error", "tool_error")
    assert "secret stack trace" not in out.text


def test_non_text_parts_become_placeholders() -> None:
    result = RawResult(is_error=False, parts=[("text", "hi"), ("image", ""), ("resource", "")])
    conn = FakeConnection(["a"], result=result)
    source = _build(conn, ["a"])
    out = asyncio.run(source.call("a", {"x": 1}))
    assert out.ok and out.text == "hi\n[image]\n[resource]"
    assert conn.calls == [("a", {"x": 1})]


def test_timeout_then_next_call_succeeds() -> None:
    conn = FakeConnection(["a"], delay=5.0)
    source = _build(conn, ["a"], timeout=1.0)
    start = time.monotonic()
    out = asyncio.run(source.call("a", {}))
    assert time.monotonic() - start < 1.5
    assert not out.ok and out.code == "tool_timeout"
    assert "srv" in out.text
    conn.delay = 0.0
    assert asyncio.run(source.call("a", {})).ok


def test_any_exception_becomes_outcome(caplog: pytest.LogCaptureFixture) -> None:
    source = _build(FakeConnection(["a"], raises=RuntimeError("boom")), ["a"])
    with caplog.at_level(logging.DEBUG, logger="ota_server.mcp.source"):
        out = asyncio.run(source.call("a", {}))
    assert out == ToolOutcome(False, "tool failed", "mcp_error")
    assert "boom" not in out.text
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "'a'" in message and "RuntimeError" in message
    # No server-controlled text: no exception message and no traceback.
    assert all("boom" not in r.getMessage() and not r.exc_info for r in caplog.records)


def _default(func: Any, name: str) -> object:
    return inspect.signature(func).parameters[name].default


def test_shared_names_have_one_home() -> None:
    assert hub.TOOL_NAME is types.TOOL_NAME
    assert isinstance(hooks.ALLOW_ALL, hooks.AllowAll)
    assert _default(McpSource, "tool_filter") is hooks.ALLOW_ALL
    assert _default(McpSource.build, "tool_filter") is hooks.ALLOW_ALL
    assert _default(McpRegistry.start, "policy") is hooks.ALLOW_ALL
    assert _default(McpRegistry.start, "tool_filter") is hooks.ALLOW_ALL


def test_caller_cancel_is_reraised() -> None:
    source = _build(FakeConnection(["a"], delay=5.0), ["a"])

    async def run() -> None:
        task = asyncio.create_task(source.call("a", {}))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()

    asyncio.run(run())

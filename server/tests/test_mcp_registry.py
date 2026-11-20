"""T5: McpRegistry starts all servers in parallel under a connect deadline, drops failed and
empty sources, and closes every connection on shutdown (B1, E1, F4, M4, D10)."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import AsyncExitStack
from typing import Any

import pytest
from mcp_fakes import FakeServer, assert_pid_gone, free_port, stdio_config

from ota_server.mcp import registry
from ota_server.mcp.adapters import SdkConnection
from ota_server.mcp.config import McpServerConfig
from ota_server.mcp.hooks import ConnectionPolicy
from ota_server.mcp.port import McpConnection, RawResult, RawTool, Verifier
from ota_server.mcp.registry import McpRegistry
from ota_server.mcp.source import McpSource

TOOLS = [RawTool("echo", "", {"type": "object"}), RawTool("other", "", {"type": "object"})]


class FakeConn:
    """A port fake that records close and can fail on close."""

    def __init__(self, fail_close: bool = False) -> None:
        self.closed = False
        self.fail_close = fail_close
        self.verify: Verifier | None = None

    def set_verifier(self, verify: Verifier) -> None:
        self.verify = verify

    async def list_tools(self) -> list[RawTool]:
        return list(TOOLS)

    async def call_tool(self, name: str, args: dict[str, Any]) -> RawResult:
        return RawResult(False, [("text", name)])

    async def close(self) -> None:
        self.closed = True
        if self.fail_close:
            raise RuntimeError("close failed")


class Recording:
    """Wraps a real SdkConnection and records that close ran."""

    def __init__(self, inner: SdkConnection) -> None:
        self.inner = inner
        self.closed = False

    def set_verifier(self, verify: Verifier) -> None:
        self.inner.set_verifier(verify)

    async def list_tools(self) -> list[RawTool]:
        return await self.inner.list_tools()

    async def call_tool(self, name: str, args: dict[str, Any]) -> RawResult:
        return await self.inner.call_tool(name, args)

    async def close(self) -> None:
        self.closed = True
        await self.inner.close()


PIN = "sha256:" + "0" * 64


def fake_config(name: str, tools: list[str] | None = None) -> McpServerConfig:
    pins = dict.fromkeys(tools or ["echo"], PIN)
    return McpServerConfig(name, "http", url="http://127.0.0.1:1/mcp", tools=pins)


def with_tools(config: McpServerConfig, tools: list[str]) -> McpServerConfig:
    return McpServerConfig(
        config.name,
        config.transport,
        url=config.url,
        command=config.command,
        args=config.args,
        env=config.env,
        tools=dict.fromkeys(tools, PIN),
    )


async def _start(configs: list[McpServerConfig], connect: Any = None) -> list[McpSource]:
    async with AsyncExitStack() as stack:
        if connect is None:
            return await McpRegistry.start(configs, stack)
        return await McpRegistry.start(configs, stack, connect=connect)


def test_one_server_down_one_up(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> list[tuple[str, list[str]]]:
        server = FakeServer("http")
        await server.start()
        try:
            up = with_tools(server.config("up"), ["echo"])
            down_url = f"http://127.0.0.1:{free_port()}/mcp"
            down = McpServerConfig("down", "http", url=down_url, tools={"echo": PIN})
            async with AsyncExitStack() as stack:
                sources = await McpRegistry.start([down, up], stack)
                return [(s.name, [spec.name for spec in s.specs()]) for s in sources]
        finally:
            await server.stop()

    with caplog.at_level(logging.WARNING, logger="ota_server.mcp.registry"):
        result = asyncio.run(scenario())
    assert result == [("up", ["echo"])]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("down" in message for message in warnings)


def test_hung_server_hits_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "CONNECT_TIMEOUT", 0.5)

    async def connect(config: McpServerConfig, policy: ConnectionPolicy) -> McpConnection:
        if config.name == "hung":
            await asyncio.Event().wait()
        return FakeConn()

    begin = time.monotonic()
    sources = asyncio.run(_start([fake_config("hung"), fake_config("ok")], connect))
    assert time.monotonic() - begin < 1.0
    assert [s.name for s in sources] == ["ok"]


class HungListConn(FakeConn):
    """Connects at once but never answers tools/list."""

    async def list_tools(self) -> list[RawTool]:
        await asyncio.Event().wait()
        return []


def test_hung_tool_list_hits_the_deadline(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(registry, "CONNECT_TIMEOUT", 0.5)
    hung = HungListConn()

    async def connect(config: McpServerConfig, policy: ConnectionPolicy) -> McpConnection:
        return hung if config.name == "hung" else FakeConn()

    begin = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="ota_server.mcp.registry"):
        sources = asyncio.run(_start([fake_config("hung"), fake_config("ok")], connect))
    assert time.monotonic() - begin < 1.0
    assert [s.name for s in sources] == ["ok"]
    assert hung.closed
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("hung" in message for message in warnings)


def test_default_deadline_is_five_seconds() -> None:
    assert registry.CONNECT_TIMEOUT == 5.0


def test_servers_connect_in_parallel() -> None:
    async def connect(config: McpServerConfig, policy: ConnectionPolicy) -> McpConnection:
        await asyncio.sleep(0.3)
        return FakeConn()

    configs = [fake_config(name) for name in ("a", "b", "c")]
    begin = time.monotonic()
    sources = asyncio.run(_start(configs, connect))
    assert time.monotonic() - begin < 0.8
    assert [s.name for s in sources] == ["a", "b", "c"]


def test_no_configs_gives_no_sources() -> None:
    assert asyncio.run(_start([])) == []


def test_source_with_no_admitted_tools_is_dropped() -> None:
    conns: dict[str, FakeConn] = {}

    async def connect(config: McpServerConfig, policy: ConnectionPolicy) -> McpConnection:
        conns[config.name] = FakeConn()
        return conns[config.name]

    configs = [fake_config("empty", ["missing"]), fake_config("full")]
    sources = asyncio.run(_start(configs, connect))
    assert [s.name for s in sources] == ["full"]
    assert conns["empty"].closed


def test_shutdown_closes_everything() -> None:
    conns: list[Recording] = []

    async def connect(config: McpServerConfig, policy: ConnectionPolicy) -> McpConnection:
        conn = Recording(await SdkConnection.open(config, policy))
        conns.append(conn)
        return conn

    async def scenario() -> int:
        server = FakeServer("http")
        await server.start()
        try:
            http = with_tools(server.config("remote"), ["echo"])
            local = with_tools(stdio_config("local"), ["echo", "pid"])
            async with AsyncExitStack() as stack:
                sources = await McpRegistry.start([http, local], stack, connect=connect)
                assert [s.name for s in sources] == ["remote", "local"]
                outcome = await sources[1].call("pid", {})
            return int(outcome.text)
        finally:
            await server.stop()

    pid = asyncio.run(scenario())
    assert len(conns) == 2
    assert all(conn.closed for conn in conns)
    assert_pid_gone(pid)


def test_one_close_fails_the_rest_still_close() -> None:
    first = FakeConn(fail_close=True)
    second = FakeConn()

    async def connect(config: McpServerConfig, policy: ConnectionPolicy) -> McpConnection:
        if config.name == "first":
            await asyncio.sleep(0.05)  # registered last, so the stack closes it first
            return first
        return second

    asyncio.run(_start([fake_config("first"), fake_config("second")], connect))
    assert first.closed
    assert second.closed


def test_bad_names_dropped_before_connect(caplog: pytest.LogCaptureFixture) -> None:
    connected: list[str] = []

    async def connect(config: McpServerConfig, policy: ConnectionPolicy) -> McpConnection:
        connected.append(config.name)
        return FakeConn()

    configs = [fake_config("a"), fake_config("a"), fake_config("b__x"), fake_config("c")]
    with caplog.at_level(logging.ERROR, logger="ota_server.mcp.registry"):
        sources = asyncio.run(_start(configs, connect))
    assert connected == ["a", "c"]
    assert [s.name for s in sources] == ["a", "c"]
    assert len(caplog.records) == 2

"""T3: SdkConnection over http, sse and stdio against fake FastMCP servers (M1, M4, D4, D9,
D11, T7) and the mcp==1.30.0 pin (D3)."""

import asyncio
import logging
import time
import tomllib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from mcp.types import ListToolsResult, Tool
from mcp_fakes import (
    AlwaysRedirectServer,
    FakeServer,
    RedirectAfterServer,
    assert_pid_gone,
    stdio_config,
)

from ota_server.hub.types import ToolOutcome
from ota_server.mcp import adapters
from ota_server.mcp.adapters import (
    RedirectBlocked,
    SdkConnection,
    Streams,
    guarded_client,
    is_transport_error,
    sdk_transport,
)
from ota_server.mcp.config import McpServerConfig
from ota_server.mcp.hooks import AllowAll, ConnectionDenied
from ota_server.mcp.port import RawResult
from ota_server.mcp.source import McpSource

SERVER_DIR = Path(__file__).resolve().parent.parent
SECRETS = ("OTA_DEVICE_ID", "MCP_SECRET_X", "OPENAI_API_KEY")


async def _list_and_call(config: McpServerConfig) -> tuple[list[str], RawResult]:
    conn = await SdkConnection.open(config, AllowAll())
    try:
        tools = await conn.list_tools()
        result = await conn.call_tool("echo", {"text": "hi"})
    finally:
        await conn.close()
    return [t.name for t in tools], result


async def _remote_list_and_call(transport: str) -> tuple[list[str], RawResult]:
    server = FakeServer(transport)
    await server.start()
    try:
        return await _list_and_call(server.config())
    finally:
        await server.stop()


@pytest.mark.parametrize("transport", ["http", "sse", "stdio"])
def test_list_and_call_over_each_transport(transport: str) -> None:
    if transport == "stdio":
        names, result = asyncio.run(_list_and_call(stdio_config()))
    else:
        names, result = asyncio.run(_remote_list_and_call(transport))
    assert "echo" in names
    assert result == RawResult(False, [("text", "hi")])


async def _call_text(config: McpServerConfig, tool: str) -> str:
    conn = await SdkConnection.open(config, AllowAll())
    try:
        result = await conn.call_tool(tool, {})
    finally:
        await conn.close()
    return result.parts[0][1]


def test_stdio_child_has_clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in SECRETS:
        monkeypatch.setenv(name, "secret-value")
    text = asyncio.run(_call_text(stdio_config(env={"FOO": "bar"}), "env_dump"))
    names = set(text.splitlines())
    assert {"FOO", "PATH"} <= names
    assert names.isdisjoint(SECRETS)


class RecordingPolicy:
    def __init__(self, order: list[str], deny: bool = False) -> None:
        self.order = order
        self.deny = deny

    async def before_connect(self, config: McpServerConfig) -> dict[str, str]:
        self.order.append("policy")
        if self.deny:
            raise ConnectionDenied(config.name)
        return {"X-Test": "1"}


def _recording_factory(order: list[str]) -> adapters.TransportFactory:
    @asynccontextmanager
    async def factory(config: McpServerConfig, headers: dict[str, str]) -> AsyncIterator[Streams]:
        order.append("transport")
        async with sdk_transport(config, headers) as streams:
            yield streams

    return factory


async def _open_close(config: McpServerConfig, policy: RecordingPolicy, order: list[str]) -> None:
    conn = await SdkConnection.open(config, policy, transport_factory=_recording_factory(order))
    await conn.close()


def test_policy_runs_before_connect() -> None:
    order: list[str] = []
    asyncio.run(_open_close(stdio_config(), RecordingPolicy(order), order))
    assert order == ["policy", "transport"]


def test_policy_denies_connection() -> None:
    order: list[str] = []
    with pytest.raises(ConnectionDenied):
        asyncio.run(_open_close(stdio_config(), RecordingPolicy(order, deny=True), order))
    assert order == ["policy"]


async def _pid_then_close(conn: SdkConnection) -> tuple[int, float]:
    try:
        result = await conn.call_tool("pid", {})
    finally:
        start = time.monotonic()
        await conn.close()
    return int(result.parts[0][1]), time.monotonic() - start


def test_close_leaves_no_stdio_child() -> None:
    async def run() -> tuple[int, float]:
        conn = await SdkConnection.open(stdio_config(), AllowAll())
        return await _pid_then_close(conn)

    pid, _ = asyncio.run(run())
    assert_pid_gone(pid)


@asynccontextmanager
async def _blocking_exit(
    config: McpServerConfig, headers: dict[str, str]
) -> AsyncIterator[Streams]:
    """Enter the real transport; on exit block until cancelled, then run the real exit."""
    async with sdk_transport(config, headers) as streams:
        try:
            yield streams
        finally:
            await asyncio.Event().wait()


def test_close_when_stop_deadline_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "STOP_TIMEOUT", 0.2)

    async def run() -> tuple[int, float]:
        conn = await SdkConnection.open(
            stdio_config(), AllowAll(), transport_factory=_blocking_exit
        )
        return await _pid_then_close(conn)

    pid, elapsed = asyncio.run(run())
    assert 0.2 <= elapsed < 0.2 + 2.0  # the cancel path ran, within the bound
    assert_pid_gone(pid)


def test_close_is_idempotent() -> None:
    async def run() -> None:
        conn = await SdkConnection.open(stdio_config(), AllowAll())
        await conn.close()
        await conn.close()

    asyncio.run(run())


class EndlessPages:
    """A session fake whose tools/list always has a next page."""

    def __init__(self) -> None:
        self.pages = 0

    async def list_tools(self, cursor: str | None = None) -> ListToolsResult:
        self.pages += 1
        if self.pages > 100:  # bounds the test if the cap is missing
            raise RuntimeError("no page cap")
        tool = Tool(name=f"t{self.pages}", inputSchema={"type": "object"})
        return ListToolsResult(tools=[tool], nextCursor=f"c{self.pages}")


def test_list_tools_stops_at_page_cap(caplog: pytest.LogCaptureFixture) -> None:
    async def run() -> tuple[int, int]:
        conn = SdkConnection(stdio_config(), AllowAll(), sdk_transport)
        session = EndlessPages()
        owner = asyncio.create_task(asyncio.Event().wait())
        conn._session = cast(Any, session)
        conn._task = cast(Any, owner)
        try:
            tools = await conn.list_tools()
        finally:
            owner.cancel()
        return len(tools), session.pages

    with caplog.at_level(logging.WARNING, logger="ota_server.mcp.adapters"):
        count, pages = asyncio.run(run())
    assert adapters.MAX_TOOL_PAGES == 20
    assert pages == count == adapters.MAX_TOOL_PAGES
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_mcp_pinned_in_pyproject_and_lock() -> None:
    project = tomllib.loads((SERVER_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    assert "mcp==1.30.0" in project["project"]["dependencies"]
    lock = tomllib.loads((SERVER_DIR / "uv.lock").read_text(encoding="utf-8"))
    versions = [p["version"] for p in lock["package"] if p["name"] == "mcp"]
    assert versions == ["1.30.0"]


# Tool-safety T4 (D6, M3): every 3xx on http and sse is blocked.

REDIRECT_DEADLINE = 2.0  # seconds a connect to a redirecting server may take
PIN = "sha256:" + "0" * 64


async def _open_redirected(transport: str) -> tuple[BaseException, float]:
    async with AlwaysRedirectServer() as server:
        start = time.monotonic()
        try:
            conn = await SdkConnection.open(server.config(transport), AllowAll())
        except Exception as exc:
            return exc, time.monotonic() - start
        await conn.close()
    raise AssertionError("connect to a redirecting server succeeded")


@pytest.mark.parametrize("transport", ["http", "sse"])
def test_redirect_on_connect_fails_fast(transport: str) -> None:
    exc, elapsed = asyncio.run(_open_redirected(transport))
    assert isinstance(exc, RedirectBlocked)
    assert elapsed < REDIRECT_DEADLINE


async def _call_through_redirect() -> tuple[ToolOutcome, ToolOutcome, ToolOutcome]:
    server = RedirectAfterServer("http")
    await server.start()
    config = McpServerConfig("fake", "http", url=server.url, tools={"echo": PIN})
    conn = await SdkConnection.open(config, AllowAll())
    try:
        source = McpSource(config, conn, await conn.list_tools())
        before = await source.call("echo", {"text": "a"})
        server.redirect_after = server.requests
        during = await source.call("echo", {"text": "b"})
        server.redirect_after = None
        after = await source.call("echo", {"text": "c"})
    finally:
        await conn.close()
        await server.stop()
    return before, during, after


def test_redirect_on_mid_session_call() -> None:
    before, during, after = asyncio.run(_call_through_redirect())
    assert before == ToolOutcome(True, "a")
    assert not during.ok
    assert during.code == "mcp_error"
    assert isinstance(after, ToolOutcome)
    assert after.ok or after.code is not None


def test_redirect_blocked_is_not_a_transport_error() -> None:
    assert not is_transport_error(RedirectBlocked("307"))
    assert not issubclass(RedirectBlocked, httpx.TransportError)


def test_guarded_client_passes_timeout_and_headers() -> None:
    async def run() -> None:
        async with guarded_client(headers={"X-A": "1"}, timeout=httpx.Timeout(3)) as client:
            assert client.headers["X-A"] == "1"
            assert client.timeout == httpx.Timeout(3)
            assert client.follow_redirects is False

    asyncio.run(run())


def test_guarded_client_default_timeout() -> None:
    async def run() -> httpx.Timeout:
        async with guarded_client() as client:
            return client.timeout

    assert asyncio.run(run()) == httpx.Timeout(30.0, read=300.0)


def test_guarded_client_blocks_a_plain_redirect() -> None:
    async def run() -> int:
        async with AlwaysRedirectServer() as server, guarded_client() as client:
            with pytest.raises(RedirectBlocked):
                await client.get(server.url("http"), follow_redirects=True)
            return server.requests

    assert asyncio.run(run()) == 1

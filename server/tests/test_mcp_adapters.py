"""T3: SdkConnection over http, sse and stdio against fake FastMCP servers (M1, M4, D4, D9,
D11, T7) and the mcp==1.30.0 pin (D3)."""

import asyncio
import time
import tomllib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp_fakes import FakeServer, assert_pid_gone, stdio_config

from ota_server.mcp import adapters
from ota_server.mcp.adapters import SdkConnection, Streams, sdk_transport
from ota_server.mcp.config import McpServerConfig
from ota_server.mcp.hooks import AllowAll, ConnectionDenied
from ota_server.mcp.port import RawResult

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


def test_mcp_pinned_in_pyproject_and_lock() -> None:
    project = tomllib.loads((SERVER_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    assert "mcp==1.30.0" in project["project"]["dependencies"]
    lock = tomllib.loads((SERVER_DIR / "uv.lock").read_text(encoding="utf-8"))
    versions = [p["version"] for p in lock["package"] if p["name"] == "mcp"]
    assert versions == ["1.30.0"]

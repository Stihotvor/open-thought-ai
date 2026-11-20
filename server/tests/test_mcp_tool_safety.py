"""Tool-safety integration (T9, D9): the real SafeConnectionPolicy, HashToolFilter and
guarded SDK transport together over a real local HTTP path. The fakes listen on 127.0.0.1,
which is on the allowlist (M3, M4, F1)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import pytest
from mcp_fakes import AlwaysRedirectServer, FakeServer, HeaderRecordingServer, build_fake

from ota_server import config as ota_config
from ota_server.config import Settings
from ota_server.hub.device_source import DeviceSource
from ota_server.hub.hub import McpHub
from ota_server.hub.pending import PendingCalls
from ota_server.hub.types import CallCtx, ToolCall, ToolOutcome, ToolSource
from ota_server.main import start_mcp_sources
from ota_server.mcp.adapters import RedirectBlocked, SdkConnection
from ota_server.mcp.config import McpServerConfig
from ota_server.mcp.policy import SafeConnectionPolicy
from ota_server.mcp.port import RawTool
from ota_server.mcp.registry import McpRegistry
from ota_server.mcp.toolfilter import HashToolFilter, tool_hash

LOOPBACK = "127.0.0.1"
ALLOWED = frozenset({LOOPBACK})
REDIRECT_DEADLINE = 2.0  # M3: seconds a connect to a redirecting server may take
SECRET_VAR = "MCP_SECRET_TEST"
SECRET = "tok-12345"
DENIED_HOST = "internal.example"
DENIED_IP = "10.0.0.9"


async def fake_pins() -> dict[str, str]:
    """The owner's pins: the current hash of each tool that build_fake lists (D3)."""
    tools = await build_fake().list_tools()
    return {t.name: tool_hash(RawTool(t.name, t.description or "", t.inputSchema)) for t in tools}


async def fake_resolver(host: str) -> list[str]:
    """The denied host resolves to a private IP; any other name has no address."""
    if host == DENIED_HOST:
        return [DENIED_IP]
    raise OSError(f"no address for {host}")


def instant_device() -> DeviceSource:
    """A DeviceSource whose device answers every tool.call at once with ok."""
    pending = PendingCalls()

    async def send(frame: dict[str, Any]) -> None:
        pending.resolve({"type": "tool.result", "id": frame["id"], "ok": True, "result": "done"})

    return DeviceSource(send, pending)


async def hub_run(hub: McpHub, name: str, args: dict[str, Any]) -> ToolOutcome:
    return await hub.run(ToolCall("c1", name, args), CallCtx())


def no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """model_env() must read no real env file: only the test's process variables count."""
    monkeypatch.setattr(ota_config, "ENV_FILE", str(tmp_path / "absent.env"))


def settings_for(tmp_path: Path, entries: list[dict[str, Any]]) -> Settings:
    path = tmp_path / "servers.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return Settings(
        _env_file=None,  # type: ignore[call-arg]  # pydantic-settings init option
        device_id="test",
        device_token_sha256=hashlib.sha256(b"token").hexdigest(),
        mcp_servers_file=str(path),
        mcp_allowed_hosts=LOOPBACK,
    )


# M3: a same-origin 307 makes the connect fail fast.


async def _open_redirected(transport: str) -> tuple[BaseException, float, int]:
    policy = SafeConnectionPolicy(ALLOWED, resolver=fake_resolver, environ=lambda: {})
    async with AlwaysRedirectServer() as server:
        start = time.monotonic()
        try:
            conn = await SdkConnection.open(server.config(transport), policy)
        except Exception as exc:
            return exc, time.monotonic() - start, server.requests
        await conn.close()
    raise AssertionError("connect to a redirecting server succeeded")


@pytest.mark.parametrize("transport", ["http", "sse"])
def test_redirect_fails_the_connect(transport: str) -> None:
    exc, elapsed, requests = asyncio.run(_open_redirected(transport))
    assert isinstance(exc, RedirectBlocked)
    assert elapsed < REDIRECT_DEADLINE
    assert requests == 1  # the 307 is never followed


# M4: the secret header reaches the server and not the logs.


async def _call_with_secret(settings: Settings, server: HeaderRecordingServer) -> ToolOutcome:
    async with AsyncExitStack() as stack:
        sources = await start_mcp_sources(settings, stack)
        assert [source.name for source in sources] == ["fake"]
        return await hub_run(McpHub(sources), "fake__echo", {"text": "hi"})


def test_secret_header_reaches_server_not_logs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(SECRET_VAR, SECRET)
    no_env_file(monkeypatch, tmp_path)

    async def run() -> tuple[ToolOutcome, list[dict[str, str]]]:
        server = HeaderRecordingServer("http")
        await server.start()
        try:
            entry = {
                "name": "fake",
                "transport": "http",
                "url": server.url,
                "tools": {"echo": (await fake_pins())["echo"]},
                "headers": {"Authorization": SECRET_VAR},
            }
            outcome = await _call_with_secret(settings_for(tmp_path, [entry]), server)
        finally:
            await server.stop()
        return outcome, server.seen

    with caplog.at_level(logging.DEBUG):
        outcome, seen = asyncio.run(run())
    assert outcome.ok and outcome.text.endswith("hi")
    assert seen
    assert all(headers.get("authorization") == SECRET for headers in seen)
    assert caplog.text.count(SECRET) == 0


# F1: a denied server does not stop startup or the other tools.


async def _start_with_denied() -> tuple[list[str], ToolOutcome, ToolOutcome]:
    server = FakeServer("http")
    await server.start()
    pins = await fake_pins()
    denied = McpServerConfig(
        "inner", "http", url=f"https://{DENIED_HOST}/mcp", tools={"echo": pins["echo"]}
    )
    good = McpServerConfig("fake", "http", url=server.url, tools={"echo": pins["echo"]})
    policy = SafeConnectionPolicy(ALLOWED, resolver=fake_resolver, environ=lambda: {})
    tool_filter = HashToolFilter({c.name: c.tools for c in (denied, good)})
    try:
        async with AsyncExitStack() as stack:
            sources = await McpRegistry.start([denied, good], stack, policy, tool_filter)
            hub_sources: list[ToolSource] = [instant_device(), *sources]
            hub = McpHub(hub_sources)
            echo = await hub_run(hub, "fake__echo", {"text": "still here"})
            volume = await hub_run(hub, "device__set_volume", {"level": 30})
            return [source.name for source in sources], echo, volume
    finally:
        await server.stop()


def test_denied_server_does_not_stop_startup(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        names, echo, volume = asyncio.run(_start_with_denied())
    assert names == ["fake"]
    assert echo.ok and echo.text.endswith("still here")  # the hub marks it untrusted
    assert volume.ok and volume.text.endswith("done")
    denials = [r.getMessage() for r in caplog.records if "connection denied" in r.getMessage()]
    assert denials == [
        f"mcp inner: connection denied (host {DENIED_HOST}): blocked address (private)"
    ]


# M3 through the lifespan helper: the redirecting server is off, the good one works.


async def _start_redirect_and_good(tmp_path: Path) -> tuple[list[str], float]:
    server = FakeServer("http")
    await server.start()
    pins = await fake_pins()
    try:
        async with AlwaysRedirectServer() as redirect:
            entries = [
                {"name": "redir", "transport": "http", "url": redirect.url("http"), "tools": pins},
                {"name": "fake", "transport": "http", "url": server.url, "tools": pins},
            ]
            start = time.monotonic()
            async with AsyncExitStack() as stack:
                sources = await start_mcp_sources(settings_for(tmp_path, entries), stack)
                elapsed = time.monotonic() - start
                return [source.name for source in sources], elapsed
    finally:
        await server.stop()


def test_redirecting_server_is_off_at_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    no_env_file(monkeypatch, tmp_path)
    names, elapsed = asyncio.run(_start_redirect_and_good(tmp_path))
    assert names == ["fake"]
    assert elapsed < REDIRECT_DEADLINE

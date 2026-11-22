"""QA cases that need a fake server on 127.0.0.1 (no real network, no real secrets)."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import AsyncExitStack
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from mcp_fakes import AlwaysRedirectServer, FakeServer, HeaderRecordingServer, RedirectAfterServer
from qa_support import (
    EMPTY,
    LOOPBACK,
    PIN0,
    FakeResolver,
    ReplyServer,
    as_resolver,
    fake_pins,
    remote,
    run_tool,
    settings_for,
)

from ota_server import config as ota_config
from ota_server.config import Settings
from ota_server.hub.device_source import DeviceSource
from ota_server.hub.hub import McpHub
from ota_server.hub.pending import PendingCalls
from ota_server.hub.types import ToolOutcome
from ota_server.main import start_mcp_sources
from ota_server.mcp.adapters import RedirectBlocked, SdkConnection, sdk_transport
from ota_server.mcp.config import McpServerConfig, load_servers
from ota_server.mcp.hooks import ALLOW_ALL, ConnectionPolicy
from ota_server.mcp.policy import SafeConnectionPolicy
from ota_server.mcp.port import McpConnection
from ota_server.mcp.registry import McpRegistry
from ota_server.mcp.source import McpSource
from ota_server.mcp.toolfilter import HashToolFilter

SECRET = "s3cr3t-Value-42"
ALLOWED = frozenset({LOOPBACK})
DEADLINE = 20.0


def no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ota_config, "ENV_FILE", str(tmp_path / "absent.env"))


async def start_and_echo(settings: Settings, name: str = "fake") -> tuple[list[str], ToolOutcome]:
    async with AsyncExitStack() as stack:
        sources = await start_mcp_sources(settings, stack)
        outcome = await run_tool(McpHub(sources), f"{name}__echo", {"text": "hi"})
        return [s.name for s in sources], outcome


async def secret_entry(server: FakeServer, **over: Any) -> dict[str, Any]:
    pins = await fake_pins()
    entry: dict[str, Any] = {
        "name": "fake",
        "transport": "http",
        "url": server.url,
        "tools": {"echo": pins["echo"]},
        "headers": {"X-Api-Key": "MCP_SECRET_N8N"},
        "no_confirm": ["echo"],
    }
    return entry | over


def test_qa_020_dns_failure_denies_one_server_only(caplog: pytest.LogCaptureFixture) -> None:
    async def run() -> list[str]:
        server = FakeServer("http")
        await server.start()
        pins = await fake_pins()
        alpha = McpServerConfig("alpha", "http", url="https://alpha.example/mcp", tools=pins)
        beta = McpServerConfig("beta", "http", url=server.url, tools={"echo": pins["echo"]})
        resolver = as_resolver(FakeResolver(raises=OSError("no dns")))
        policy = SafeConnectionPolicy(ALLOWED, resolver, lambda: {})
        tool_filter = HashToolFilter({"alpha": alpha.tools, "beta": beta.tools})
        try:
            async with AsyncExitStack() as stack:
                sources = await McpRegistry.start([alpha, beta], stack, policy, tool_filter)
                return [s.full_name for source in sources for s in source.specs()]
        finally:
            await server.stop()

    with caplog.at_level(logging.WARNING):
        listed = asyncio.run(run())
    assert listed == ["beta__echo"]
    assert any(
        "alpha" in r.getMessage() and "dns failure" in r.getMessage() for r in caplog.records
    )


class FlipPolicy:
    """Allows the first connect, then applies `later` to every reconnect."""

    def __init__(self, later: ConnectionPolicy) -> None:
        self.later = later
        self.denied = False
        self.checks = 0

    async def before_connect(self, config: McpServerConfig) -> dict[str, str]:
        if not self.denied:
            return {}
        self.checks += 1
        return await self.later.before_connect(config)


class BoomPolicy:
    def __init__(self) -> None:
        self.armed = False

    async def before_connect(self, config: McpServerConfig) -> dict[str, str]:
        if self.armed:
            raise RuntimeError("policy bug")
        return {}


async def _dropped_then_called(policy: Any, caplog_name: str) -> tuple[list[ToolOutcome], bool]:
    server = FakeServer("http")
    await server.start()
    config = McpServerConfig(
        "alpha", "http", url=server.url, tools={"echo": PIN0}, no_confirm=["echo"]
    )
    conn = await SdkConnection.open(config, policy)
    try:
        source = McpSource(config, conn, await conn.list_tools())
        hub = McpHub([source])
        policy.denied = True
        policy.armed = True
        await server.stop()  # the session drops: the next call reconnects
        first = await asyncio.wait_for(run_tool(hub, "alpha__echo", {"text": "a"}), DEADLINE)
        second = await asyncio.wait_for(run_tool(hub, "alpha__echo", {"text": "b"}), DEADLINE)
        return [first, second], conn._closed
    finally:
        await conn.close()


def test_qa_028_reconnect_reruns_the_ssrf_check(caplog: pytest.LogCaptureFixture) -> None:
    later = SafeConnectionPolicy(EMPTY, as_resolver(FakeResolver()), lambda: {})
    flip = FlipPolicy(later)
    with caplog.at_level(logging.WARNING):
        outcomes, closed = asyncio.run(_dropped_then_called(flip, "alpha"))
    assert all(not o.ok for o in outcomes)
    assert flip.checks >= 2 and not closed
    assert any("connection denied" in r.getMessage() for r in caplog.records)


def test_qa_076_policy_exception_is_a_deny_and_run_does_not_raise() -> None:
    outcomes, closed = asyncio.run(_dropped_then_called(BoomPolicy(), "alpha"))
    assert all(not o.ok and o.code == "mcp_error" for o in outcomes)
    assert not closed


def test_qa_030_redirect_to_another_origin_is_not_followed() -> None:
    async def run() -> tuple[BaseException | None, int, int]:
        async with AlwaysRedirectServer() as other:
            location = other.url("http")
            async with ReplyServer(307, {"Location": location}) as first:
                try:
                    conn = await SdkConnection.open(first.config("http"), ALLOW_ALL)
                except Exception as exc:
                    return exc, first.requests, other.requests
                await conn.close()
                return None, first.requests, other.requests

    exc, first_requests, other_requests = asyncio.run(run())
    assert isinstance(exc, RedirectBlocked)
    assert first_requests >= 1
    assert other_requests == 0


def test_qa_032_redirect_after_reconnect_fails_again() -> None:
    async def run() -> tuple[list[ToolOutcome], bool]:
        server = RedirectAfterServer("http")
        await server.start()
        config = McpServerConfig(
            "alpha", "http", url=server.url, tools={"echo": PIN0}, no_confirm=["echo"]
        )
        conn = await SdkConnection.open(config, ALLOW_ALL)
        try:
            hub = McpHub([McpSource(config, conn, await conn.list_tools())])
            server.redirect_after = server.requests
            first = await run_tool(hub, "alpha__echo", {"text": "a"})
            second = await run_tool(hub, "alpha__echo", {"text": "b"})
            return [first, second], conn._closed
        finally:
            await conn.close()
            await server.stop()

    outcomes, closed = asyncio.run(asyncio.wait_for(run(), DEADLINE))
    assert [o.ok for o in outcomes] == [False, False] and not closed


def test_qa_037_secret_not_logged_on_failed_connect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("MCP_SECRET_N8N", SECRET)
    no_env_file(monkeypatch, tmp_path)

    async def run() -> list[str]:
        async with ReplyServer(401, body=b"denied") as server:
            entry = {
                "name": "fake",
                "transport": "http",
                "url": server.url("http"),
                "tools": {"echo": (await fake_pins())["echo"]},
                "headers": {"X-Api-Key": "MCP_SECRET_N8N"},
                "no_confirm": ["echo"],
            }
            names, _ = await start_and_echo(settings_for(tmp_path, [entry]))
            return names

    with caplog.at_level(logging.DEBUG):
        names = asyncio.run(asyncio.wait_for(run(), DEADLINE))
    assert names == []
    assert any(r.levelno >= logging.WARNING for r in caplog.records)
    assert caplog.text.count(SECRET) == 0


def test_qa_038_missing_secret_denies_the_entry_and_others_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("MCP_SECRET_MISSING", raising=False)
    monkeypatch.setenv("MCP_SECRET_B", "b-secret-value")
    no_env_file(monkeypatch, tmp_path)

    async def run() -> list[str]:
        server = FakeServer("http")
        await server.start()
        try:
            a = await secret_entry(server, name="a", headers={"X-K": "MCP_SECRET_MISSING"})
            b = await secret_entry(server, name="b", headers={"X-K": "MCP_SECRET_B"})
            names, _ = await start_and_echo(settings_for(tmp_path, [a, b]), "b")
            return names
        finally:
            await server.stop()

    with caplog.at_level(logging.DEBUG):
        names = asyncio.run(run())
    assert names == ["b"]
    assert "MCP_SECRET_MISSING" in caplog.text
    assert "b-secret-value" not in caplog.text


async def _seen_header(settings: Settings, server: HeaderRecordingServer) -> list[str | None]:
    names, outcome = await start_and_echo(settings)
    assert names == ["fake"] and outcome.ok
    return [headers.get("x-api-key") for headers in server.seen]


async def _with_recorder(tmp_path: Path) -> list[str | None]:
    server = HeaderRecordingServer("http")
    await server.start()
    try:
        entry = await secret_entry(server)
        return await _seen_header(settings_for(tmp_path, [entry]), server)
    finally:
        await server.stop()


def test_qa_040_secret_only_in_the_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / "test.env"
    env_file.write_text("MCP_SECRET_N8N=from-dotenv\n", encoding="utf-8")
    monkeypatch.setattr(ota_config, "ENV_FILE", str(env_file))
    monkeypatch.delenv("MCP_SECRET_N8N", raising=False)
    seen = asyncio.run(_with_recorder(tmp_path))
    assert seen and set(seen) == {"from-dotenv"}


def test_qa_041_process_variable_wins_over_the_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / "test.env"
    env_file.write_text("MCP_SECRET_N8N=from-dotenv\n", encoding="utf-8")
    monkeypatch.setattr(ota_config, "ENV_FILE", str(env_file))
    monkeypatch.setenv("MCP_SECRET_N8N", "from-process")
    seen = asyncio.run(_with_recorder(tmp_path))
    assert seen and set(seen) == {"from-process"}


def test_qa_083_device_source_name_cannot_be_taken(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    no_env_file(monkeypatch, tmp_path)

    async def send(frame: dict[str, Any]) -> None:
        return None

    device = DeviceSource(send, PendingCalls())
    baseline = [t["function"]["name"] for t in McpHub([device]).list_tools()]

    async def run() -> list[str]:
        server = FakeServer("http")
        await server.start()
        try:
            pins = await fake_pins()
            entry = {"name": "device", "transport": "http", "url": server.url, "tools": pins}
            settings = settings_for(tmp_path, [entry])
            async with AsyncExitStack() as stack:
                sources = await start_mcp_sources(settings, stack)
                hub = McpHub([device, *sources])
                return [t["function"]["name"] for t in hub.list_tools()]
        finally:
            await server.stop()

    assert asyncio.run(run()) == baseline


def test_qa_090_n8n_config_only_setup(tmp_path: Path) -> None:
    """Allowlist n8n, an MCP_SECRET_N8N variable and a config entry; the fake serves n8n."""

    async def run() -> tuple[list[str], list[str | None]]:
        server = HeaderRecordingServer("http")
        await server.start()
        pins = await fake_pins()
        entry = {
            "name": "n8n",
            "transport": "http",
            "url": "http://n8n:5678/mcp",
            "tools": {"echo": pins["echo"]},
            "headers": {"X-Api-Key": "MCP_SECRET_N8N"},
            "no_confirm": ["echo"],
        }
        settings = settings_for(tmp_path, [entry], allowed="n8n")
        configs = load_servers(settings.mcp_servers_file)
        policy = SafeConnectionPolicy(
            settings.allowed_hosts, environ=lambda: {"MCP_SECRET_N8N": "tok"}
        )

        async def connect(config: McpServerConfig, pol: ConnectionPolicy) -> McpConnection:
            def factory(cfg: McpServerConfig, headers: dict[str, str]) -> Any:
                return sdk_transport(replace(cfg, url=server.url), headers)

            return await SdkConnection.open(config, pol, transport_factory=factory)

        try:
            async with AsyncExitStack() as stack:
                tool_filter = HashToolFilter({c.name: c.tools for c in configs})
                sources = await McpRegistry.start(
                    configs, stack, policy, tool_filter, connect=connect
                )
                listed = [s.full_name for source in sources for s in source.specs()]
                return listed, [h.get("x-api-key") for h in server.seen]
        finally:
            await server.stop()

    listed, seen = asyncio.run(asyncio.wait_for(run(), DEADLINE))
    assert listed == ["n8n__echo"]
    assert seen and set(seen) == {"tok"}


def test_qa_092_no_error_body_for_a_failed_call(caplog: pytest.LogCaptureFixture) -> None:
    body = b"internal path /srv/db.sqlite"

    async def run() -> ToolOutcome:
        server = FakeServer("http")
        await server.start()
        config = McpServerConfig(
            "fake", "http", url=server.url, tools={"echo": PIN0}, no_confirm=["echo"]
        )
        conn = await SdkConnection.open(config, ALLOW_ALL)
        try:
            hub = McpHub([McpSource(config, conn, await conn.list_tools())])
            await server.stop()
            failing = ReplyServer(500, body=body)
            # Serve the 500 on the old port: the call reconnects there.
            failing._server = await asyncio.start_server(failing._handle, LOOPBACK, server.port)
            try:
                return await asyncio.wait_for(run_tool(hub, "fake__echo", {"text": "x"}), DEADLINE)
            finally:
                await failing.stop()
        finally:
            await conn.close()

    with caplog.at_level(logging.DEBUG):
        outcome = asyncio.run(run())
    assert not outcome.ok
    assert "/srv/db.sqlite" not in outcome.text
    assert "/srv/db.sqlite" not in caplog.text


def test_qa_033_redirect_deadline_is_bounded() -> None:
    """The sse redirect fails within 2 s (also covered by test_redirect_fails_the_connect)."""

    async def run() -> float:
        async with AlwaysRedirectServer() as server:
            start = time.monotonic()
            with pytest.raises(RedirectBlocked):
                await SdkConnection.open(remote(server.url("sse"), transport="sse"), ALLOW_ALL)
            return time.monotonic() - start

    assert asyncio.run(run()) < 2.0

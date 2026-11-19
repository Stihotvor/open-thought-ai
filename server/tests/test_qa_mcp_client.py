"""QA-001..QA-048 of docs/design/mcp-client/qa/cases.md, one test per case id."""

import asyncio
import base64
import json
import logging
import os
import signal
import socket
import sys
import time
import tomllib
from collections.abc import AsyncIterator, Awaitable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any, cast

import pytest
import uvicorn
from fastapi import WebSocket
from mcp.server.fastmcp import FastMCP
from mcp.types import (
    ContentBlock,
    EmbeddedResource,
    ImageContent,
    TextContent,
    TextResourceContents,
)
from mcp_fakes import assert_pid_gone, free_port
from pydantic import AnyUrl

from ota_server.config import Settings
from ota_server.hub.steps import UNTRUSTED_PREFIX
from ota_server.hub.types import ToolCall, ToolOutcome
from ota_server.main import start_mcp_sources
from ota_server.mcp.adapters import SdkConnection
from ota_server.mcp.config import McpServerConfig, load_servers
from ota_server.mcp.hooks import ConnectionDenied, ConnectionPolicy
from ota_server.mcp.port import McpConnection, RawTool
from ota_server.mcp.registry import McpRegistry
from ota_server.mcp.source import McpSource
from ota_server.session import DeviceSession

TESTS = Path(__file__).resolve().parent
SERVER_DIR = TESTS.parent
STDIO = str(TESTS / "mcp_qa_stdio_server.py")
RAW = str(TESTS / "mcp_qa_raw_server.py")
LONG_TOOL = "t" * 60
LOOPBACK = ("127.0.0.1", "::1")


@pytest.fixture(autouse=True)
def loopback_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """T9: refuse any non-loopback inet connect from the test process."""
    real = socket.socket.connect

    def guard(self: socket.socket, address: Any) -> None:
        if self.family in (socket.AF_INET, socket.AF_INET6) and address[0] not in LOOPBACK:
            raise OSError(f"QA guard: non-loopback connect to {address[0]} refused")
        real(self, address)

    monkeypatch.setattr(socket.socket, "connect", guard)


def run[T](coro: Awaitable[T]) -> T:
    async def bounded() -> T:
        return await asyncio.wait_for(coro, 90)

    return asyncio.run(bounded())


# ---------------------------------------------------------------- fake HTTP/SSE server


class QaServer:
    """A FastMCP fake with many tools, served by uvicorn on 127.0.0.1. Spies on traffic."""

    def __init__(self, transport: str = "http") -> None:
        self.transport = transport
        self.port = free_port()
        self.calls: list[str] = []  # tool names that reached the server
        self.headers: list[dict[str, str]] = []
        self.inits = 0  # connections: POSTs without a session id (http), GET /sse (sse)
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}{'/mcp' if self.transport == 'http' else '/sse'}"

    def entry(self, name: str, tools: list[str], timeout: float = 10) -> dict[str, Any]:
        return {"name": name, "transport": self.transport, "url": self.url,
                "tools": tools, "timeout": timeout}  # fmt: skip

    def config(self, name: str, tools: list[str], timeout: float = 10) -> McpServerConfig:
        return McpServerConfig(name, self.transport, url=self.url, tools=tools, timeout=timeout)

    def _build(self) -> FastMCP:  # noqa: C901
        mcp = FastMCP("qa-fake")
        calls = self.calls

        @mcp.tool()
        def add(a: int, b: int) -> str:
            calls.append("add")
            return str(a + b)

        @mcp.tool()
        def secret_op() -> str:
            calls.append("secret_op")
            return "secret"

        @mcp.tool()
        def delete_all() -> str:
            calls.append("delete_all")
            return "deleted"

        @mcp.tool()
        def ping() -> str:
            return "pong"

        @mcp.tool()
        def forecast(city: str) -> str:
            return "sunny"

        @mcp.tool()
        def send_mail() -> str:
            return "sent"

        @mcp.tool()
        def echo(text: str) -> str:
            return text

        @mcp.tool()
        def boom() -> str:
            raise ValueError("Traceback: password=hunter2 at /srv/app.py")

        @mcp.tool()
        def chart() -> list[ContentBlock]:
            data = base64.b64encode(b"\x89PNG" + b"0" * 40_000).decode()
            return [
                TextContent(type="text", text="Chart:"),
                ImageContent(type="image", data=data, mimeType="image/png"),
            ]

        @mcp.tool()
        def res() -> list[ContentBlock]:
            contents = TextResourceContents(uri=AnyUrl("file:///a.txt"), text="RESOURCEBODY")
            return [EmbeddedResource(type="resource", resource=contents)]

        @mcp.tool()
        async def slow_echo(text: str) -> str:
            await asyncio.sleep(0.5)
            return text

        @mcp.tool()
        async def sleep30() -> str:
            await asyncio.sleep(30)
            return "late"

        @mcp.tool()
        def big() -> str:
            return "x" * 10_000

        @mcp.tool()
        def ok_tool() -> str:
            return "ok"

        @mcp.tool(name="bad.tool")
        def bad_tool() -> str:
            return "bad"

        @mcp.tool(name=LONG_TOOL)
        def long_tool() -> str:
            return "long"

        return mcp

    async def start(self) -> None:
        mcp = self._build()
        app = mcp.streamable_http_app() if self.transport == "http" else mcp.sse_app()
        inner: Any = app

        async def spy(scope: Any, receive: Any, send: Any) -> None:
            if scope["type"] == "http":
                hdrs = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
                self.headers.append(hdrs)
                if self.transport == "http":
                    if scope["method"] == "POST" and "mcp-session-id" not in hdrs:
                        self.inits += 1
                elif scope["method"] == "GET" and scope["path"] == "/sse":
                    self.inits += 1
            await inner(scope, receive, send)

        cfg = uvicorn.Config(
            spy, host="127.0.0.1", port=self.port, log_level="warning", timeout_graceful_shutdown=1
        )
        self._server = uvicorn.Server(cfg)
        self._task = asyncio.create_task(self._server.serve())
        deadline = time.monotonic() + 5
        while not self._server.started:
            if self._task.done() or time.monotonic() >= deadline:
                raise RuntimeError("fake server did not start")
            await asyncio.sleep(0.01)

    async def stop(self) -> None:
        if self._server is not None and self._task is not None:
            self._server.should_exit = True
            await self._task
            self._server = self._task = None

    async def restart(self) -> None:
        await self.stop()
        await self.start()


@asynccontextmanager
async def serving(transport: str = "http") -> AsyncIterator[QaServer]:
    server = QaServer(transport)
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


# ---------------------------------------------------------------- helpers


class StubWs:
    """A device socket stand-in: it answers every tool.call frame with ok."""

    def __init__(self) -> None:
        self.session: DeviceSession | None = None

    async def send_json(self, msg: dict[str, Any]) -> None:
        if msg.get("type") == "tool.call" and self.session is not None:
            self.session.pending.resolve({"id": msg["id"], "ok": True, "result": "up"})


def new_session(sources: list[McpSource]) -> DeviceSession:
    ws = StubWs()
    session = DeviceSession(cast(WebSocket, ws), mcp_sources=sources)
    ws.session = session
    return session


def names(session: DeviceSession) -> list[str]:
    return [t["function"]["name"] for t in session.turn_tools().list_tools()]


async def call(session: DeviceSession, name: str, **args: Any) -> ToolOutcome:
    return await session.turn_tools().run(ToolCall("c1", name, args))


def body(outcome: ToolOutcome) -> str:
    assert outcome.text.startswith(UNTRUSTED_PREFIX)
    return outcome.text[len(UNTRUSTED_PREFIX) :]


def stdio_entry(
    name: str,
    tools: list[str],
    *extra: str,
    env: dict[str, str] | None = None,
    script: str = STDIO,
) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name, "transport": "stdio", "command": sys.executable,
                             "args": [script, *extra], "tools": tools}  # fmt: skip
    if env is not None:
        entry["env"] = env
    return entry


def stdio_config(name: str, tools: list[str], *extra: str, **kw: Any) -> McpServerConfig:
    entry = stdio_entry(name, tools, *extra, **kw)
    return McpServerConfig(name, "stdio", command=entry["command"], args=entry["args"],
                           env=entry.get("env", {}), tools=tools)  # fmt: skip


async def start_file(tmp_path: Path, entries: Any, stack: AsyncExitStack) -> list[McpSource]:
    """Server startup (main.start_mcp_sources) with a config file holding entries."""
    path = tmp_path / "servers.json"
    path.write_text(entries if isinstance(entries, str) else json.dumps(entries))
    return await start_mcp_sources(Settings.model_construct(mcp_servers_file=str(path)), stack)


def errors(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]


def warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def tool_names(sources: list[McpSource]) -> list[str]:
    return [spec.full_name for s in sources for spec in s.specs()]


# ---------------------------------------------------------------- functional: transports


def test_qa_001_http_list_and_call() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            sources = await McpRegistry.start([server.config("notes", ["add"])], stack)
            session = new_session(sources)
            out = await call(session, "notes__add", a=2, b=3)
            assert out.ok
            assert body(out) == "5"
            assert [n for n in names(session) if n.startswith("notes__")] == ["notes__add"]

    run(scenario())


def test_qa_002_sse_list_and_call() -> None:
    async def scenario() -> None:
        async with serving("sse") as server, AsyncExitStack() as stack:
            sources = await McpRegistry.start([server.config("weather", ["forecast"])], stack)
            out = await call(new_session(sources), "weather__forecast", city="Kyiv")
            assert body(out) == "sunny"

    run(scenario())


def test_qa_003_stdio_list_and_call() -> None:
    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            sources = await McpRegistry.start([stdio_config("local", ["echo"])], stack)
            out = await call(new_session(sources), "local__echo", text="hello")
            assert body(out) == "hello"

    run(scenario())


# ---------------------------------------------------------------- allowlist


def test_qa_004_unlisted_tool_hidden() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            sources = await McpRegistry.start([server.config("notes", ["add"])], stack)
            listed = names(new_session(sources))
            assert "notes__add" in listed
            assert "notes__delete_all" not in listed

    run(scenario())


def test_qa_005_unlisted_call_unknown() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            sources = await McpRegistry.start([server.config("notes", ["add"])], stack)
            out = await call(new_session(sources), "notes__delete_all")
            assert (out.ok, out.code) == (False, "unknown_tool")
            assert "delete_all" not in server.calls
            # the source itself is also closed to a direct call
            direct = await sources[0].call("delete_all", {})
            assert direct.code == "unknown_tool"
            assert server.calls == []

    run(scenario())


def test_qa_006_allowlist_missing_tool(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            sources = await McpRegistry.start([server.config("notes", ["add", "ghost"])], stack)
            assert any("ghost" in w for w in warnings(caplog))
            out = await call(new_session(sources), "notes__add", a=1, b=2)
            assert body(out) == "3"

    run(scenario())


# ---------------------------------------------------------------- startup failures


def test_qa_007_server_down_at_startup(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    async def scenario() -> None:
        async with serving() as up, AsyncExitStack() as stack:
            down = {"name": "down", "transport": "http", "url": "http://127.0.0.1:9/mcp",
                    "tools": ["ping"]}  # fmt: skip
            sources = await start_file(tmp_path, [up.entry("up", ["ping"]), down], stack)
            assert any("down" in w for w in warnings(caplog))
            listed = names(new_session(sources))
            assert "up__ping" in listed
            assert "device__get_status" in listed
            assert not [n for n in listed if n.startswith("down__")]

    run(scenario())


def test_qa_008_down_stays_off(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = QaServer()
        async with AsyncExitStack() as stack:
            sources = await start_file(tmp_path, [server.entry("down", ["ping"])], stack)
            await server.start()
            try:
                assert not [n for n in names(new_session(sources)) if n.startswith("down__")]
                assert server.inits == 0  # nobody retried
            finally:
                await server.stop()

    run(scenario())


# ---------------------------------------------------------------- result mapping


def test_qa_009_is_error_generic() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            sources = await McpRegistry.start([server.config("notes", ["boom"])], stack)
            out = await call(new_session(sources), "notes__boom")
            assert not out.ok
            assert "hunter2" not in out.text
            assert "/srv/app.py" not in out.text
            assert len(out.text) < 200

    run(scenario())


def test_qa_010_image_replaced() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            sources = await McpRegistry.start([server.config("notes", ["chart"])], stack)
            out = await call(new_session(sources), "notes__chart")
            assert body(out) == "Chart:\n[image]"
            assert "PNG" not in out.text

    run(scenario())


def test_qa_011_resource_replaced() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            sources = await McpRegistry.start([server.config("notes", ["res"])], stack)
            out = await call(new_session(sources), "notes__res")
            assert "[resource]" in out.text
            assert "RESOURCEBODY" not in out.text

    run(scenario())


# ---------------------------------------------------------------- config file


def test_qa_012_invalid_json(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            sources = await start_file(tmp_path, "{not json", stack)
            assert sources == []
            assert errors(caplog)
            session = new_session(sources)
            assert body(await call(session, "device__get_status")) == "up"

    run(scenario())


def test_qa_013_one_bad_entry_skipped(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    bad = {"name": "b", "transport": "ftp", "url": "ftp://x/", "tools": ["echo"]}

    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            entries = [stdio_entry("a", ["echo"]), bad, stdio_entry("c", ["echo"])]
            sources = await start_file(tmp_path, entries, stack)
            assert any("'b'" in e for e in errors(caplog))
            assert tool_names(sources) == ["a__echo", "c__echo"]

    run(scenario())


def test_qa_014_duplicate_name(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            first = server.entry("notes", ["add"])
            second = {**first, "url": "http://127.0.0.1:9/mcp"}
            sources = await start_file(tmp_path, [first, second], stack)
            assert any("duplicate" in e for e in errors(caplog))
            assert tool_names(sources) == ["notes__add"]
            assert server.inits == 1

    run(scenario())


def test_qa_015_bad_server_names(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            entries = [stdio_entry(n, ["echo"]) for n in ("my__srv", "bad name!", "n" * 70)]
            assert await start_file(tmp_path, entries, stack) == []
            assert len(errors(caplog)) >= 3

    run(scenario())


def test_qa_016_bad_tool_name(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            entry = server.entry("notes", ["bad.tool", "ok_tool"])
            sources = await start_file(tmp_path, [entry], stack)
            assert tool_names(sources) == ["notes__ok_tool"]
            assert any("bad.tool" in e for e in errors(caplog))

    run(scenario())


def test_qa_017_combined_name_too_long(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            entry = server.entry("notes", [LONG_TOOL, "ok_tool"])
            sources = await start_file(tmp_path, [entry], stack)
            assert tool_names(sources) == ["notes__ok_tool"]
            assert any(LONG_TOOL in e for e in errors(caplog))

    run(scenario())


# ---------------------------------------------------------------- concurrency, timeout


def test_qa_018_concurrent_sessions() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            sources = await McpRegistry.start([server.config("notes", ["slow_echo"])], stack)
            a, b = new_session(sources), new_session(sources)
            out_a, out_b = await asyncio.gather(
                call(a, "notes__slow_echo", text="alpha"), call(b, "notes__slow_echo", text="beta")
            )
            assert (body(out_a), body(out_b)) == ("alpha", "beta")
            assert server.inits == 1

    run(scenario())


def test_qa_019_timeout() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            sources = await McpRegistry.start([server.config("notes", ["sleep30"], 2)], stack)
            start = time.monotonic()
            out = await call(new_session(sources), "notes__sleep30")
            assert time.monotonic() - start <= 2.5
            assert (out.ok, out.code) == (False, "tool_timeout")

    run(scenario())


def test_qa_020_call_after_timeout() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            cfg = server.config("notes", ["sleep30", "add"], 2)
            sources = await McpRegistry.start([cfg], stack)
            session = new_session(sources)
            assert (await call(session, "notes__sleep30")).code == "tool_timeout"
            assert body(await call(session, "notes__add", a=1, b=1)) == "2"
            assert server.inits == 1

    run(scenario())


# ---------------------------------------------------------------- http reconnect


async def recording_start(
    configs: list[McpServerConfig], stack: AsyncExitStack, conns: list[SdkConnection]
) -> list[McpSource]:
    async def connect(config: McpServerConfig, policy: ConnectionPolicy) -> McpConnection:
        conn = await SdkConnection.open(config, policy)
        conns.append(conn)
        return conn

    return await McpRegistry.start(configs, stack, connect=connect)


def test_qa_021_http_restart_reconnect() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            conns: list[SdkConnection] = []
            sources = await recording_start([server.config("notes", ["add"])], stack, conns)
            await server.restart()
            out = await call(new_session(sources), "notes__add", a=4, b=4)
            assert body(out) == "8"
            assert conns[0].generation == 1
            assert server.inits == 2

    run(scenario())


def test_qa_022_reconnect_fails() -> None:
    async def scenario() -> None:
        server = QaServer()
        await server.start()
        async with AsyncExitStack() as stack:
            sources = await McpRegistry.start([server.config("notes", ["add"])], stack)
            await server.stop()
            session = new_session(sources)
            out = await call(session, "notes__add", a=1, b=1)
            assert not out.ok
            assert out.code == "mcp_error"
            assert body(await call(session, "device__get_status")) == "up"

    run(scenario())


def test_qa_023_concurrent_failed_calls_one_reconnect() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            conns: list[SdkConnection] = []
            sources = await recording_start([server.config("notes", ["add"])], stack, conns)
            await server.restart()
            a, b = new_session(sources), new_session(sources)
            out_a, out_b = await asyncio.gather(
                call(a, "notes__add", a=1, b=2), call(b, "notes__add", a=3, b=4)
            )
            assert (body(out_a), body(out_b)) == ("3", "7")
            assert conns[0].generation == 1
            assert server.inits == 2

    run(scenario())


# ---------------------------------------------------------------- stdio lifecycle


def test_qa_024_stdio_child_restarts() -> None:
    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            sources = await McpRegistry.start([stdio_config("local", ["echo", "pid"])], stack)
            session = new_session(sources)
            old = int(body(await call(session, "local__pid")))
            os.kill(old, signal.SIGKILL)
            assert_pid_gone(old)
            assert body(await call(session, "local__echo", text="again")) == "again"
            assert int(body(await call(session, "local__pid"))) != old

    run(scenario())


def test_qa_025_stdio_keeps_crashing(tmp_path: Path) -> None:
    flag = tmp_path / "crash"

    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            cfg = stdio_config("local", ["echo", "pid"], "--crash-if", str(flag))
            sources = await McpRegistry.start([cfg], stack)
            session = new_session(sources)
            pid = int(body(await call(session, "local__pid")))
            flag.write_text("x")
            os.kill(pid, signal.SIGKILL)
            assert_pid_gone(pid)
            for _ in range(2):
                out = await call(session, "local__echo", text="x")
                assert not out.ok
            assert body(await call(session, "device__get_status")) == "up"

    run(scenario())


def test_qa_026_shutdown_leaves_no_children(tmp_path: Path) -> None:
    async def scenario() -> list[int]:
        pids: list[int] = []
        async with serving() as server:
            async with AsyncExitStack() as stack:
                configs = [stdio_config(f"s{i}", ["pid"]) for i in range(2)]
                sources = await McpRegistry.start([*configs, server.config("n", ["add"])], stack)
                session = new_session(sources)
                for i in range(2):
                    pids.append(int(body(await call(session, f"s{i}__pid"))))
                assert len(set(pids)) == 2
            return pids

    for pid in run(scenario()):
        assert_pid_gone(pid)


def test_qa_027_shutdown_with_stubborn_child() -> None:
    async def scenario() -> tuple[int, float]:
        stack = AsyncExitStack()
        sources = await McpRegistry.start([stdio_config("local", ["pid"], "--stubborn")], stack)
        pid = int(body(await call(new_session(sources), "local__pid")))
        start = time.monotonic()
        await stack.aclose()
        return pid, time.monotonic() - start

    pid, elapsed = run(scenario())
    assert elapsed <= 5 + 3
    assert_pid_gone(pid)


def test_qa_028_malformed_reply_becomes_error(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            cfg = McpServerConfig(
                "raw", "stdio", command=sys.executable, args=[RAW], tools=["boom"], timeout=5
            )
            sources = await McpRegistry.start([cfg], stack)
            out = await call(new_session(sources), "raw__boom")
            assert not out.ok
            assert out.code in ("mcp_error", "tool_timeout")

    run(scenario())


def test_qa_029_caller_cancel() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            cfg = server.config("notes", ["sleep30", "add"], 30)
            sources = await McpRegistry.start([cfg], stack)
            session = new_session(sources)
            task = asyncio.create_task(call(session, "notes__sleep30"))
            await asyncio.sleep(1)
            start = time.monotonic()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert time.monotonic() - start < 1
            assert body(await call(session, "notes__add", a=1, b=2)) == "3"

    run(scenario())


# ---------------------------------------------------------------- config variable and fields


def test_qa_030_missing_variable(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            sources = await start_mcp_sources(Settings.model_construct(mcp_servers_file=""), stack)
            assert sources == []
            assert errors(caplog) == []
            assert "device__get_status" in names(new_session(sources))

    run(scenario())


def test_qa_031_path_missing(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            settings = Settings.model_construct(mcp_servers_file="/nonexistent/servers.json")
            sources = await start_mcp_sources(settings, stack)
            assert sources == []
            assert errors(caplog)
            assert body(await call(new_session(sources), "device__get_status")) == "up"

    run(scenario())


def test_qa_032_timeout_bounds(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            low = {**stdio_entry("low", ["echo"]), "timeout": 0.5}
            high = {**stdio_entry("high", ["echo"]), "timeout": 61}
            sources = await start_file(tmp_path, [low, high, stdio_entry("dflt", ["echo"])], stack)
            assert len(errors(caplog)) >= 2
            assert [s.name for s in sources] == ["dflt"]
            assert sources[0].timeout == 10

    run(scenario())


def test_qa_033_url_userinfo_or_query(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            urls = ["http://user:pw@127.0.0.1:8000/mcp", "http://127.0.0.1:8000/mcp?token=abc"]
            entries = [
                {"name": f"u{i}", "transport": "http", "url": u, "tools": ["add"]}
                for i, u in enumerate(urls)
            ]
            assert await start_file(tmp_path, entries, stack) == []
            assert len(errors(caplog)) >= 2
            assert "user:pw@" not in caplog.text

    run(scenario())


def test_qa_034_non_http_scheme(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    entry = {"name": "f", "transport": "http", "url": "file:///etc/passwd", "tools": ["x"]}
    path = tmp_path / "s.json"
    path.write_text(json.dumps([entry]))
    assert load_servers(str(path)) == []
    assert errors(caplog)


def test_qa_035_missing_fields(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    no_command = {"name": "a", "transport": "stdio", "args": [], "tools": ["x"]}
    no_url = {"name": "b", "transport": "http", "tools": ["x"]}
    no_tools = {k: v for k, v in stdio_entry("c", ["x"]).items() if k != "tools"}
    path = tmp_path / "s.json"
    path.write_text(json.dumps([no_command, no_url, no_tools]))
    assert load_servers(str(path)) == []
    assert len(errors(caplog)) >= 3


# ---------------------------------------------------------------- stdio details


def test_qa_036_child_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OTA_TOKEN", "abc")
    monkeypatch.setenv("MCP_SECRET_X", "zzz")

    async def scenario() -> str:
        async with AsyncExitStack() as stack:
            cfg = stdio_config("local", ["env_vars"], env={"MODE": "fast"})
            sources = await McpRegistry.start([cfg], stack)
            return body(await call(new_session(sources), "local__env_vars"))

    env = run(scenario())
    assert "MODE=fast" in env.splitlines()
    assert any(line.startswith("PATH=") for line in env.splitlines())
    for secret in ("OPENAI_API_KEY", "OTA_TOKEN", "MCP_SECRET_X"):
        assert secret not in env


def test_qa_037_stdio_args() -> None:
    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            cfg = stdio_config("local", ["whoami"], "--name", "x")
            sources = await McpRegistry.start([cfg], stack)
            assert tool_names(sources) == ["local__whoami"]
            assert body(await call(new_session(sources), "local__whoami")) == "x"

    run(scenario())


def test_qa_038_connections_shared() -> None:
    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            sources = await McpRegistry.start([stdio_config("local", ["pid"])], stack)
            pids = set()
            for _ in range(3):
                pids.add(body(await call(new_session(sources), "local__pid")))
            assert len(pids) == 1

    run(scenario())


def test_qa_039_zero_allowed_tools(tmp_path: Path) -> None:
    async def scenario() -> list[int]:
        files = [tmp_path / "p1", tmp_path / "p2"]
        async with AsyncExitStack() as stack:
            e1 = stdio_entry("ghosty", ["ghost"], env={"QA_PIDFILE": str(files[0])})
            e2 = stdio_entry("empty", [], env={"QA_PIDFILE": str(files[1])})
            sources = await start_file(tmp_path, [e1, e2], stack)
            assert sources == []
            return [int(f.read_text()) for f in files]

    for pid in run(scenario()):
        assert_pid_gone(pid)


# ---------------------------------------------------------------- hub limits


def test_qa_040_long_result_truncated() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            sources = await McpRegistry.start([server.config("notes", ["big"])], stack)
            out = await call(new_session(sources), "notes__big")
            assert out.ok
            assert len(out.text) == 2000

    run(scenario())


def test_qa_041_device_tools_unchanged() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            sources = await McpRegistry.start([server.config("notes", ["add"])], stack)
            session = new_session(sources)
            assert body(await call(session, "device__get_status")) == "up"
            listed = names(session)
            assert {"device__set_volume", "device__get_status", "notes__add"} <= set(listed)

    run(scenario())


# ---------------------------------------------------------------- seam (#31)


class DenyAll:
    async def before_connect(self, config: McpServerConfig) -> dict[str, str]:
        raise ConnectionDenied(config.name)


class HeaderPolicy:
    async def before_connect(self, config: McpServerConfig) -> dict[str, str]:
        return {"X-Test": "1"}


class RejectAdd:
    def admit(self, server: str, tool: RawTool) -> bool:
        return tool.name != "add"


def test_qa_042_connection_denied(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            cfg = server.config("notes", ["add"])
            sources = await McpRegistry.start([cfg], stack, policy=DenyAll())
            assert sources == []
            assert server.inits == 0
            assert any("notes" in w for w in warnings(caplog))

    run(scenario())


def test_qa_043_filter_and_headers() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            sources = await McpRegistry.start(
                [server.config("notes", ["add", "echo"])],
                stack,
                policy=HeaderPolicy(),
                tool_filter=RejectAdd(),
            )
            assert any(h.get("x-test") == "1" for h in server.headers)
            assert tool_names(sources) == ["notes__echo"]

    run(scenario())


def test_qa_044_n8n_entry_config_only(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            sources = await start_file(tmp_path, [server.entry("n8n", ["send_mail"])], stack)
            assert tool_names(sources) == ["n8n__send_mail"]

    run(scenario())


# ---------------------------------------------------------------- gate and security


def test_qa_045_quality_gate_is_run_outside_pytest() -> None:
    pytest.skip("NOT AUTOMATED here: the gate (ruff, mypy, pytest --cov) is run as commands")


def test_qa_046_sdk_pin() -> None:
    project = tomllib.loads((SERVER_DIR / "pyproject.toml").read_text())
    pins = [d for d in project["project"]["dependencies"] if d.replace(" ", "").startswith("mcp")]
    assert pins == ["mcp==1.30.0"]
    lock = tomllib.loads((SERVER_DIR / "uv.lock").read_text())
    versions = [p["version"] for p in lock["package"] if p["name"] == "mcp"]
    assert versions == ["1.30.0"]


def test_qa_047_loopback_only() -> None:
    with pytest.raises(OSError, match="non-loopback"):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("8.8.8.8", 53))


def test_qa_048_secret_value_not_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            entry = stdio_entry("local", ["echo"], env={"API_KEY": "sk-live-123"})
            sources = await start_file(tmp_path, [entry], stack)
            assert tool_names(sources) == ["local__echo"]
            await call(new_session(sources), "local__echo", text="x")

    run(scenario())
    assert "sk-live-123" not in caplog.text

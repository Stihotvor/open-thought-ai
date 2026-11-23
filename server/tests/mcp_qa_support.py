"""Shared helpers for the mcp-client QA modules (test_qa_mcp_*.py): fakes, guard, run()."""

import asyncio
import base64
import importlib.util
import json
import logging
import socket
import sys
import time
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
from mcp_fakes import free_port
from pydantic import AnyUrl

from ota_server.config import Settings
from ota_server.hub.steps import UNTRUSTED_PREFIX
from ota_server.hub.types import ToolCall, ToolOutcome
from ota_server.main import start_mcp_sources
from ota_server.mcp.config import McpServerConfig
from ota_server.mcp.port import RawTool
from ota_server.mcp.source import McpSource
from ota_server.mcp.toolfilter import tool_hash
from ota_server.session import DeviceSession

TESTS = Path(__file__).resolve().parent
SERVER_DIR = TESTS.parent
STDIO = str(TESTS / "mcp_qa_stdio_server.py")
RAW = str(TESTS / "mcp_qa_raw_server.py")
LONG_TOOL = "t" * 60
LOOPBACK = ("127.0.0.1", "::1")
PIN = "sha256:" + "0" * 64  # a well-formed pin that matches no tool
ALLOWED = "127.0.0.1"  # the fakes run on loopback: on the MCP allowlist (tool-safety D9)


def pins(tools: list[str]) -> dict[str, str]:
    """The servers file tools map for a list of tool names, with the owner's real pins (D3).
    A name no fake serves gets the dummy PIN."""
    return {tool: KNOWN_PINS.get(tool, PIN) for tool in tools}


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
                "tools": pins(tools), "no_confirm": list(tools), "timeout": timeout}  # fmt: skip

    def config(self, name: str, tools: list[str], timeout: float = 10) -> McpServerConfig:
        return McpServerConfig(
            name, self.transport, url=self.url, tools=pins(tools), timeout=timeout,
            no_confirm=list(tools),
        )  # fmt: skip

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


# ---------------------------------------------------------------- pins


def _hashes(mcp: FastMCP) -> dict[str, str]:
    tools = asyncio.run(mcp.list_tools())
    return {t.name: tool_hash(RawTool(t.name, t.description or "", t.inputSchema)) for t in tools}


def _stdio_fake() -> FastMCP:
    """The FastMCP of mcp_qa_stdio_server.py, loaded with no options (it parses sys.argv)."""
    spec = importlib.util.spec_from_file_location("mcp_qa_stdio_tools", STDIO)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    argv = sys.argv
    sys.argv = [STDIO]
    try:
        spec.loader.exec_module(module)
    finally:
        sys.argv = argv
    fake: FastMCP = module.mcp
    return fake


# The QA http fake and the stdio fake share only echo, defined the same in both.
KNOWN_PINS = {**_hashes(_stdio_fake()), **_hashes(QaServer()._build())}


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
                             "args": [script, *extra], "tools": pins(tools),
                             "no_confirm": list(tools)}  # fmt: skip
    if env is not None:
        entry["env"] = env
    return entry


def stdio_config(name: str, tools: list[str], *extra: str, **kw: Any) -> McpServerConfig:
    entry = stdio_entry(name, tools, *extra, **kw)
    return McpServerConfig(name, "stdio", command=entry["command"], args=entry["args"],
                           env=entry.get("env", {}), tools=pins(tools),
                           no_confirm=list(tools))  # fmt: skip


async def start_file(tmp_path: Path, entries: Any, stack: AsyncExitStack) -> list[McpSource]:
    """Server startup (main.start_mcp_sources) with a config file holding entries."""
    path = tmp_path / "servers.json"
    path.write_text(entries if isinstance(entries, str) else json.dumps(entries))
    return await start_mcp_sources(
        Settings.model_construct(mcp_servers_file=str(path), mcp_allowed_hosts=ALLOWED), stack
    )


def errors(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]


def warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def tool_names(sources: list[McpSource]) -> list[str]:
    return [spec.full_name for s in sources for spec in s.specs()]

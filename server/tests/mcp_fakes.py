"""Fake MCP servers for the adapter tests: FastMCP on 127.0.0.1 for http and sse, a stdio
child script, redirecting fakes (tool-safety T4) and the shared assert_pid_gone helper."""

from __future__ import annotations

import asyncio
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.types import ASGIApp, Receive, Scope, Send

from ota_server.mcp.config import McpServerConfig

STDIO_SCRIPT = Path(__file__).with_name("mcp_stdio_server.py")
START_TIMEOUT = 5.0
PATHS = {"http": "/mcp", "sse": "/sse"}


def _is_zombie(pid: int) -> bool:
    """True when pid has exited but its parent has not reaped it yet (Linux /proc)."""
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as stat:
            return stat.read().rsplit(")", 1)[1].split()[0] == "Z"
    except (OSError, IndexError):
        return False


def assert_pid_gone(pid: int, timeout: float = 3.0) -> None:
    """Poll every 0.05 s until the process pid is dead; fail after timeout.

    A zombie counts as dead: a caller that blocks the event loop also blocks the
    SDK from reaping the child, so the pid can stay in the table as a zombie.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        if _is_zombie(pid):
            return
        if time.monotonic() >= deadline:
            raise AssertionError(f"process {pid} still exists after {timeout} s")
        time.sleep(0.05)


def stdio_config(name: str = "local", env: dict[str, str] | None = None) -> McpServerConfig:
    """A stdio entry that runs mcp_stdio_server.py with this test's Python."""
    return McpServerConfig(
        name, "stdio", command=sys.executable, args=[str(STDIO_SCRIPT)], env=env or {}
    )


def build_fake(name: str = "fake") -> FastMCP:
    """A fresh FastMCP server with the tools echo(text) and hidden."""
    mcp = FastMCP(name)

    @mcp.tool()
    def echo(text: str) -> str:
        return text

    @mcp.tool()
    def hidden() -> str:
        return "hidden"

    return mcp


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


class FakeServer:
    """A FastMCP fake served by uvicorn in a background task on 127.0.0.1 (http or sse)."""

    def __init__(self, transport: str) -> None:
        self.transport = transport
        self.port = free_port()
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}{PATHS[self.transport]}"

    def config(self, name: str = "fake") -> McpServerConfig:
        return McpServerConfig(name, self.transport, url=self.url)

    async def start(self) -> None:
        """Serve a fresh app (a FastMCP session manager runs only once) on self.port."""
        cfg = uvicorn.Config(
            self._app(),
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
            timeout_graceful_shutdown=1,
        )
        self._server = uvicorn.Server(cfg)
        self._task = asyncio.create_task(self._server.serve())
        deadline = time.monotonic() + START_TIMEOUT
        while not self._server.started:
            if self._task.done() or time.monotonic() >= deadline:
                raise RuntimeError(f"fake {self.transport} server did not start")
            await asyncio.sleep(0.01)

    def _app(self) -> ASGIApp:
        mcp = build_fake()
        return mcp.streamable_http_app() if self.transport == "http" else mcp.sse_app()

    async def stop(self) -> None:
        if self._server is None or self._task is None:
            return
        self._server.should_exit = True
        await self._task
        self._server = None
        self._task = None

    async def restart(self) -> None:
        """Stop, then serve again on the same port."""
        await self.stop()
        await self.start()


def _redirect_target(scope: Scope) -> str:
    """A same-origin Location: the request's own host and port, another path."""
    host, port = scope["server"]
    return f"http://{host}:{port}/moved"


class RedirectAfterServer(FakeServer):
    """A FakeServer that answers normally, then a same-origin 307 to every request once
    `requests` reaches `redirect_after` (None: never)."""

    def __init__(self, transport: str, redirect_after: int | None = None) -> None:
        super().__init__(transport)
        self.redirect_after = redirect_after
        self.requests = 0

    def _app(self) -> ASGIApp:
        inner = super()._app()

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] != "http":
                await inner(scope, receive, send)
                return
            self.requests += 1
            if self.redirect_after is not None and self.requests > self.redirect_after:
                await _send_307(scope, send)
                return
            await inner(scope, receive, send)

        return app


async def _send_307(scope: Scope, send: Send) -> None:
    location = _redirect_target(scope).encode("ascii")
    headers = [(b"location", location), (b"content-length", b"0")]
    await send({"type": "http.response.start", "status": 307, "headers": headers})
    await send({"type": "http.response.body", "body": b""})


class AlwaysRedirectServer:
    """A tiny stdlib HTTP server on 127.0.0.1: a same-origin 307 to every request."""

    def __init__(self) -> None:
        self.requests = 0
        self._server: asyncio.Server | None = None
        self.port = 0

    def url(self, transport: str) -> str:
        return f"http://127.0.0.1:{self.port}{PATHS[transport]}"

    def config(self, transport: str, name: str = "redir") -> McpServerConfig:
        return McpServerConfig(name, transport, url=self.url(transport))

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def __aenter__(self) -> AlwaysRedirectServer:
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.stop()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await _read_request(reader)
            self.requests += 1
            location = f"http://127.0.0.1:{self.port}/moved"
            writer.write(
                b"HTTP/1.1 307 Temporary Redirect\r\n"
                + f"Location: {location}\r\n".encode("ascii")
                + b"Content-Length: 0\r\nConnection: close\r\n\r\n"
            )
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()


async def _read_request(reader: asyncio.StreamReader) -> None:
    """Read one request head and its Content-Length body."""
    head = await reader.readuntil(b"\r\n\r\n")
    length = 0
    for line in head.decode("latin-1").split("\r\n")[1:]:
        name, _, value = line.partition(":")
        if name.strip().lower() == "content-length":
            length = int(value.strip())
    if length:
        await reader.readexactly(length)


class HeaderRecordingServer(FakeServer):
    """A FakeServer that keeps the request headers of every http request (tool-safety T9)."""

    def __init__(self, transport: str) -> None:
        super().__init__(transport)
        self.seen: list[dict[str, str]] = []

    def _app(self) -> ASGIApp:
        inner = super()._app()

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "http":
                headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
                self.seen.append(headers)
            await inner(scope, receive, send)

        return app

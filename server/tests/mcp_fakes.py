"""Fake MCP servers for the adapter tests: FastMCP on 127.0.0.1 for http and sse, a stdio
child script, and the shared assert_pid_gone helper (T3, T4, T5, T6)."""

from __future__ import annotations

import asyncio
import os
import socket
import sys
import time
from pathlib import Path

import uvicorn
from mcp.server.fastmcp import FastMCP

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
        mcp = build_fake()
        app = mcp.streamable_http_app() if self.transport == "http" else mcp.sse_app()
        cfg = uvicorn.Config(
            app, host="127.0.0.1", port=self.port, log_level="warning", timeout_graceful_shutdown=1
        )
        self._server = uvicorn.Server(cfg)
        self._task = asyncio.create_task(self._server.serve())
        deadline = time.monotonic() + START_TIMEOUT
        while not self._server.started:
            if self._task.done() or time.monotonic() >= deadline:
                raise RuntimeError(f"fake {self.transport} server did not start")
            await asyncio.sleep(0.01)

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

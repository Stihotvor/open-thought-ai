"""SdkConnection: the McpConnection port over the mcp SDK 1.x (D2, D3, D4).

This is the only module that imports the SDK. One owner task enters the transport and the
ClientSession, and leaves them in the same task (anyio rule). Calls from other tasks use the
shared ClientSession directly, with no queue (E7).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client  # private: recheck on mcp bump
from mcp.shared.message import SessionMessage
from mcp.types import CallToolResult, ContentBlock, ImageContent, TextContent, Tool

from ota_server.mcp.config import McpServerConfig
from ota_server.mcp.hooks import ConnectionPolicy
from ota_server.mcp.port import RawResult, RawTool

STOP_TIMEOUT = 5.0  # seconds close() waits for the owner task before it cancels it (D9)

Streams = tuple[
    MemoryObjectReceiveStream[SessionMessage | Exception], MemoryObjectSendStream[SessionMessage]
]
TransportFactory = Callable[[McpServerConfig, dict[str, str]], AbstractAsyncContextManager[Streams]]


@asynccontextmanager
async def _http_transport(url: str, headers: dict[str, str]) -> AsyncIterator[Streams]:
    async with (
        create_mcp_http_client(headers=headers) as client,
        streamable_http_client(url, http_client=client) as (read, write, _),
    ):
        yield read, write


def sdk_transport(
    config: McpServerConfig, headers: dict[str, str]
) -> AbstractAsyncContextManager[Streams]:
    """The real SDK transport for config. Headers go to http and sse only (D11)."""
    if config.transport == "http":
        return _http_transport(config.url, headers)
    if config.transport == "sse":
        return sse_client(config.url, headers=headers)
    # stdio: the SDK merges get_default_environment() with config.env (D9, T7).
    params = StdioServerParameters(command=config.command, args=config.args, env=config.env)
    return stdio_client(params)


class SdkConnection:
    """One MCP server connection. Build it with `await SdkConnection.open(config, policy)`."""

    def __init__(
        self, config: McpServerConfig, policy: ConnectionPolicy, factory: TransportFactory
    ) -> None:
        self._config = config
        self._policy = policy
        self._factory = factory
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._session: ClientSession | None = None
        self._task: asyncio.Task[None] | None = None

    @classmethod
    async def open(
        cls,
        config: McpServerConfig,
        policy: ConnectionPolicy,
        *,
        transport_factory: TransportFactory = sdk_transport,
    ) -> SdkConnection:
        """Connect and initialize. Re-raises the owner's failure, e.g. ConnectionDenied."""
        conn = cls(config, policy, transport_factory)
        await conn._start()
        return conn

    async def _start(self) -> None:
        task = asyncio.create_task(self._own(), name=f"mcp-owner-{self._config.name}")
        self._task = task
        ready = asyncio.create_task(self._ready.wait())
        try:
            await asyncio.wait({task, ready}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            await self.close()
            raise
        finally:
            ready.cancel()
        if not self._ready.is_set():
            await task  # re-raises the owner's failure
            raise ConnectionError(f"mcp server {self._config.name!r} closed before ready")

    async def _own(self) -> None:
        headers = await self._policy.before_connect(self._config)
        if self._config.transport == "stdio":
            headers = {}  # for stdio the policy may only deny (D11)
        async with (
            self._factory(self._config, headers) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            self._session = session
            self._ready.set()
            await self._stop.wait()

    def _live(self) -> ClientSession:
        if self._session is None:
            raise ConnectionError(f"mcp server {self._config.name!r} is not connected")
        return self._session

    async def list_tools(self) -> list[RawTool]:
        session = self._live()
        tools: list[Tool] = []
        cursor: str | None = None
        while True:
            page = await session.list_tools(cursor)
            tools.extend(page.tools)
            cursor = page.nextCursor
            if cursor is None:
                break
        return [RawTool(t.name, t.description or "", t.inputSchema) for t in tools]

    async def call_tool(self, name: str, args: dict[str, Any]) -> RawResult:
        result = await self._live().call_tool(name, args)
        return _to_raw(result)

    async def close(self) -> None:
        """Stop the owner task; after STOP_TIMEOUT cancel it (D9). Idempotent, never raises."""
        task = self._task
        if task is None:
            return
        self._stop.set()
        if not task.done():
            done, _ = await asyncio.wait({task}, timeout=STOP_TIMEOUT)
            if not done:
                task.cancel()
                await asyncio.wait({task})
        if not task.cancelled():
            task.exception()  # mark the owner's failure as retrieved


def _to_raw(result: CallToolResult) -> RawResult:
    return RawResult(result.isError, [_part(block) for block in result.content])


def _part(block: ContentBlock) -> tuple[str, str]:
    if isinstance(block, TextContent):
        return ("text", block.text)
    if isinstance(block, ImageContent):
        return ("image", "")
    return ("resource", "")

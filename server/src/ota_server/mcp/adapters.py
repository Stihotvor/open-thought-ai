"""SdkConnection: the McpConnection port over the mcp SDK 1.x (D2, D3, D4).

This is the only module that imports the SDK. One owner task enters the transport and the
ClientSession, and leaves them in the same task (anyio rule). Calls from other tasks use the
shared ClientSession directly, with no queue (E7). A transport error makes call_tool reconnect
once under a lock and retry once (D7).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import anyio
import httpx
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client  # private: recheck on mcp bump
from mcp.shared.exceptions import McpError
from mcp.shared.message import SessionMessage
from mcp.types import (
    CONNECTION_CLOSED,
    CallToolResult,
    ContentBlock,
    ImageContent,
    TextContent,
    Tool,
)

from ota_server.mcp.config import McpServerConfig
from ota_server.mcp.hooks import ConnectionPolicy
from ota_server.mcp.port import RawResult, RawTool

log = logging.getLogger(__name__)

MAX_TOOL_PAGES = 20  # tools/list pages read at most; a server can page without end
STOP_TIMEOUT = 5.0  # seconds close() waits for the owner task before it cancels it (D9)
RECONNECT_TIMEOUT = 5.0  # seconds for one reconnect: old owner stop plus new open (D7)
# The SDK streamable http client answers a 404 for an unknown session id with this code.
SESSION_TERMINATED = 32600
TRANSPORT_ERRORS = (
    anyio.ClosedResourceError,
    anyio.BrokenResourceError,
    anyio.EndOfStream,
    httpx.TransportError,
    ConnectionError,  # includes OwnerGone and "not connected"
)

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


class OwnerGone(ConnectionError):
    """The owner task ended while a call was waiting (e.g. the transport failed)."""


class ConnectionClosed(RuntimeError):
    """The connection was closed with close(). Not a transport error: no reconnect."""


def is_transport_error(exc: BaseException) -> bool:
    """D7: only these start a reconnect. A tool fault McpError and a timeout do not."""
    if isinstance(exc, McpError):
        return exc.error.code in (CONNECTION_CLOSED, SESSION_TERMINATED)
    return isinstance(exc, TRANSPORT_ERRORS)


class SdkConnection:
    """One MCP server connection. Build it with `await SdkConnection.open(config, policy)`."""

    def __init__(
        self, config: McpServerConfig, policy: ConnectionPolicy, factory: TransportFactory
    ) -> None:
        self._config = config
        self._policy = policy
        self._factory = factory
        self._stop = asyncio.Event()
        self._session: ClientSession | None = None
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._generation = 0
        self._closed = False

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
        conn._session = await conn._start()
        return conn

    @property
    def generation(self) -> int:
        """Goes up by 1 after each successful reconnect."""
        return self._generation

    async def _start(self) -> ClientSession:
        """Start a new owner task; return its ready session. The caller publishes it."""
        ready: asyncio.Future[ClientSession] = asyncio.get_running_loop().create_future()
        stop = asyncio.Event()
        self._stop = stop
        task = asyncio.create_task(self._own(ready, stop), name=f"mcp-owner-{self._config.name}")
        self._task = task
        first: set[asyncio.Future[Any]] = {task, ready}
        try:
            await asyncio.wait(first, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            ready.cancel()
            await _cancel_owner(task)  # not ready: the stop event cannot end it
            raise
        if not ready.done():
            ready.cancel()
            await task  # re-raises the owner's failure
            raise ConnectionError(f"mcp server {self._config.name!r} closed before ready")
        return ready.result()

    async def _own(self, ready: asyncio.Future[ClientSession], stop: asyncio.Event) -> None:
        headers = await self._policy.before_connect(self._config)
        if self._config.transport == "stdio":
            headers = {}  # for stdio the policy may only deny (D11)
        async with (
            self._factory(self._config, headers) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            if not ready.done():
                ready.set_result(session)
            await stop.wait()

    def _live(self) -> tuple[ClientSession, asyncio.Task[None], int]:
        """The shared session, its owner and the generation that belongs to them."""
        if self._closed:
            raise ConnectionClosed(f"mcp server {self._config.name!r} connection is closed")
        session, task = self._session, self._task
        if session is None or task is None or task.done():
            raise ConnectionError(f"mcp server {self._config.name!r} is not connected")
        return session, task, self._generation

    async def list_tools(self) -> list[RawTool]:
        session, _, _ = self._live()
        tools: list[Tool] = []
        cursor: str | None = None
        for _ in range(MAX_TOOL_PAGES):
            page = await session.list_tools(cursor)
            tools.extend(page.tools)
            cursor = page.nextCursor
            if cursor is None:
                break
        else:
            log.warning(
                "mcp %s: tool list stopped after %d pages", self._config.name, MAX_TOOL_PAGES
            )
        return [RawTool(t.name, t.description or "", t.inputSchema) for t in tools]

    async def call_tool(self, name: str, args: dict[str, Any]) -> RawResult:
        """Call once; on a transport error reconnect and retry once (D7)."""
        seen = self._generation  # used only when no session is live
        try:
            session, owner, seen = self._live()
            return await self._call_on(session, owner, name, args)
        except Exception as exc:
            if not is_transport_error(exc):
                raise
        await self.reconnect(seen)
        session, owner, _ = self._live()
        return await self._call_on(session, owner, name, args)

    async def _call_on(
        self, session: ClientSession, owner: asyncio.Task[None], name: str, args: dict[str, Any]
    ) -> RawResult:
        """One call on session. Raises OwnerGone if the owner ends first."""
        call = asyncio.create_task(session.call_tool(name, args))
        try:
            await asyncio.wait({call, owner}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            if not call.done():
                call.cancel()
                await asyncio.wait({call})
        if call.cancelled():
            raise OwnerGone(f"mcp server {self._config.name!r} owner task ended")
        return _to_raw(call.result())

    async def reconnect(self, seen_generation: int) -> None:
        """Replace the owner once per generation. Shielded: a caller cancel or timeout does
        not stop it half done. Raises the failure; the next call then tries again."""
        job = asyncio.create_task(self._reconnect_locked(seen_generation))
        job.add_done_callback(_retrieve)
        await asyncio.shield(job)

    async def _reconnect_locked(self, seen_generation: int) -> None:
        async with self._lock:
            if self._closed or self._generation > seen_generation:
                return  # closed, or another caller already reconnected
            old_task, old_stop = self._task, self._stop
            self._session = None
            async with asyncio.timeout(RECONNECT_TIMEOUT):  # covers stop plus open (D7)
                if old_task is not None:
                    await _stop_owner(old_task, old_stop, None)
                session = await self._start()
            self._generation += 1  # publish the session and its generation together
            self._session = session

    async def close(self) -> None:
        """Stop the owner task; after STOP_TIMEOUT cancel it (D9). Waits for an in-flight
        reconnect, so no owner outlives close. Idempotent, never raises."""
        async with self._lock:
            self._closed = True
            self._session = None
            if self._task is not None:
                await _stop_owner(self._task, self._stop, STOP_TIMEOUT)


async def _stop_owner(task: asyncio.Task[None], stop: asyncio.Event, timeout: float | None) -> None:
    """Set stop and wait for the owner; cancel it after timeout or when this wait is cancelled."""
    stop.set()
    try:
        if not task.done():
            await asyncio.wait({task}, timeout=timeout)
    finally:
        await _cancel_owner(task)


async def _cancel_owner(task: asyncio.Task[None]) -> None:
    """Cancel the owner if it still runs, wait for it, and mark its failure as retrieved."""
    if not task.done():
        task.cancel()
        await asyncio.wait({task})
    _retrieve(task)


def _retrieve(task: asyncio.Task[None]) -> None:
    if not task.cancelled():
        task.exception()


def _to_raw(result: CallToolResult) -> RawResult:
    return RawResult(result.isError, [_part(block) for block in result.content])


def _part(block: ContentBlock) -> tuple[str, str]:
    if isinstance(block, TextContent):
        return ("text", block.text)
    if isinstance(block, ImageContent):
        return ("image", "")
    return ("resource", "")

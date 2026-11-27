"""SdkConnection: the McpConnection port over the mcp SDK 1.x (D2, D3, D4).

This is the only module that imports the SDK. One owner task enters the transport and the
ClientSession, and leaves them in the same task (anyio rule). Calls from other tasks use the
shared ClientSession directly, with no queue (E7). A transport error makes call_tool reconnect
once under a lock and retry once (D7); a reconnect runs the verifier on the new tool list
before the session serves a call. Every 3xx on http and sse raises RedirectBlocked
(tool-safety D6). At most MAX_CALLS_IN_FLIGHT calls run at once, and after a failed reconnect
calls fail fast for RECONNECT_COOLDOWN seconds instead of reconnecting again (#77).
"""

from __future__ import annotations

import asyncio
import logging
import time
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
from mcp.shared._httpx_utils import (  # private module: recheck on mcp bump
    MCP_DEFAULT_SSE_READ_TIMEOUT,
    MCP_DEFAULT_TIMEOUT,
)
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
from ota_server.mcp.port import RawResult, RawTool, Verifier

log = logging.getLogger(__name__)

MAX_TOOL_PAGES = 20  # tools/list pages read at most; a server can page without end
STOP_TIMEOUT = 5.0  # seconds close() waits for the owner task before it cancels it (D9)
RECONNECT_TIMEOUT = 5.0  # seconds for one reconnect: old owner stop plus new open (D7)
MAX_CALLS_IN_FLIGHT = 8  # calls one connection runs at once; the rest wait (#77)
RECONNECT_COOLDOWN = 5.0  # seconds after a failed reconnect with no new attempt (#77)
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


class RedirectBlocked(Exception):
    """A server answered a 3xx. Not an httpx.TransportError, so no reconnect starts (D6)."""


class NoRedirectTransport(httpx.AsyncBaseTransport):
    """Wraps a transport and raises RedirectBlocked on every 3xx response. The SDK follows
    same-origin redirects itself (stream_within_origin), so follow_redirects=False is not
    enough."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        if 300 <= response.status_code < 400:
            await response.aclose()
            # Only the status: the Location can carry server-controlled text.
            raise RedirectBlocked(f"HTTP {response.status_code} redirect blocked")
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


def guarded_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    """An McpHttpClientFactory: the SDK default client, but every 3xx raises (D6)."""
    return httpx.AsyncClient(
        transport=NoRedirectTransport(httpx.AsyncHTTPTransport()),
        follow_redirects=False,
        headers=headers,
        timeout=timeout or httpx.Timeout(MCP_DEFAULT_TIMEOUT, read=MCP_DEFAULT_SSE_READ_TIMEOUT),
        auth=auth,
    )


@asynccontextmanager
async def _http_transport(url: str, headers: dict[str, str]) -> AsyncIterator[Streams]:
    async with (
        guarded_client(headers=headers) as client,
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
        return sse_client(config.url, headers=headers, httpx_client_factory=guarded_client)
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
        self._verify: Verifier | None = None
        self._slots = asyncio.Semaphore(MAX_CALLS_IN_FLIGHT)
        self._retry_at = 0.0  # time.monotonic() before which no reconnect starts

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
        conn._session = await conn._start(check=False)
        return conn

    def set_verifier(self, verify: Verifier) -> None:
        """Run verify on the listed tools after each reconnect, before the session is used."""
        self._verify = verify

    @property
    def generation(self) -> int:
        """Goes up by 1 after each successful reconnect."""
        return self._generation

    async def _start(self, check: bool) -> ClientSession:
        """Start a new owner task; return its ready session. The caller publishes it.
        With check, the owner runs the verifier before the session is ready."""
        ready: asyncio.Future[ClientSession] = asyncio.get_running_loop().create_future()
        stop = asyncio.Event()
        self._stop = stop
        task = asyncio.create_task(
            self._own(ready, stop, check), name=f"mcp-owner-{self._config.name}"
        )
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

    async def _own(
        self, ready: asyncio.Future[ClientSession], stop: asyncio.Event, check: bool
    ) -> None:
        headers = await self._policy.before_connect(self._config)
        if self._config.transport == "stdio":
            headers = {}  # for stdio the policy may only deny (D11)
        try:
            await self._serve(headers, ready, stop, check)
        except BaseExceptionGroup as group:
            raise _redirect_in(group) or group from None

    async def _serve(
        self,
        headers: dict[str, str],
        ready: asyncio.Future[ClientSession],
        stop: asyncio.Event,
        check: bool,
    ) -> None:
        failure: Exception | None = None
        async with (
            self._factory(self._config, headers) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            if check:
                failure = await self._verify_on(session)
            if failure is None:
                if not ready.done():
                    ready.set_result(session)
                await stop.wait()
        if failure is not None:
            raise failure  # outside the SDK task groups, so it is not wrapped in a group

    async def _verify_on(self, session: ClientSession) -> Exception | None:
        """Run the verifier on session's tool list; return its failure, if any."""
        if self._verify is None:
            return None
        try:
            await self._verify(await self._list_on(session))
        except Exception as exc:
            return exc
        return None

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
        return await self._list_on(session)

    async def _list_on(self, session: ClientSession) -> list[RawTool]:
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
        """Call once; on a transport error reconnect and retry once (D7). Holds a slot of
        MAX_CALLS_IN_FLIGHT for the whole call; the caller's timeout covers the wait."""
        async with self._slots:
            return await self._call_with_retry(name, args)

    async def _call_with_retry(self, name: str, args: dict[str, Any]) -> RawResult:
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
        not stop it half done. Raises the failure; the next call fails fast during
        RECONNECT_COOLDOWN, then tries again."""
        job = asyncio.create_task(self._reconnect_locked(seen_generation))
        job.add_done_callback(_retrieve)
        await asyncio.shield(job)

    async def _reconnect_locked(self, seen_generation: int) -> None:
        async with self._lock:
            if self._closed or self._generation > seen_generation:
                return  # closed, or another caller already reconnected
            if time.monotonic() < self._retry_at:
                raise ConnectionError(
                    f"mcp server {self._config.name!r} is cooling down after a failed reconnect"
                )
            try:
                await self._replace_owner()
            except Exception:
                self._retry_at = time.monotonic() + RECONNECT_COOLDOWN
                raise
            self._retry_at = 0.0

    async def _replace_owner(self) -> None:
        """Stop the old owner and start a verified new one. The caller holds the lock."""
        old_task, old_stop = self._task, self._stop
        self._session = None
        async with asyncio.timeout(RECONNECT_TIMEOUT):  # covers stop plus open (D7)
            if old_task is not None:
                await _stop_owner(old_task, old_stop, None)
            session = await self._start(check=True)
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


def _redirect_in(group: BaseExceptionGroup[BaseException]) -> RedirectBlocked | None:
    """The first RedirectBlocked in the SDK task group's error, raised alone so the caller
    sees it and not a wrapped group (D6)."""
    match = group.subgroup(lambda exc: isinstance(exc, RedirectBlocked))
    while match is not None:
        first = match.exceptions[0]
        if isinstance(first, RedirectBlocked):
            return first
        match = first if isinstance(first, BaseExceptionGroup) else None
    return None


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

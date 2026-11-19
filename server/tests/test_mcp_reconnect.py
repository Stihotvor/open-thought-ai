"""T4: SdkConnection reconnect with a lock, a generation counter and one retry (D6, D7, D11,
E7, F2, F3, M2, M3)."""

import asyncio
import os
import signal
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import replace

import mcp_fakes
import pytest
from mcp.server.fastmcp import FastMCP
from mcp_fakes import FakeServer, free_port, stdio_config

from ota_server.mcp import adapters
from ota_server.mcp.adapters import ConnectionClosed, SdkConnection, Streams, sdk_transport
from ota_server.mcp.config import McpServerConfig
from ota_server.mcp.port import RawResult
from ota_server.mcp.source import McpSource

SLACK = 0.5  # seconds a bounded step may run past its deadline


class CountingPolicy:
    """A recording ConnectionPolicy: before_connect calls beyond the first are reconnects."""

    def __init__(self, order: list[str] | None = None) -> None:
        self.calls = 0
        self.order = order if order is not None else []

    async def before_connect(self, config: McpServerConfig) -> dict[str, str]:
        self.calls += 1
        self.order.append("policy")
        return {}

    @property
    def reconnects(self) -> int:
        return self.calls - 1


def _build_extended(name: str = "fake") -> FastMCP:
    """build_fake plus wait_echo (0.2 s) and slow_once (3 s on its first call only)."""
    mcp = FastMCP(name)
    slow_calls: list[int] = []

    @mcp.tool()
    def echo(text: str) -> str:
        return text

    @mcp.tool()
    async def wait_echo(text: str) -> str:
        await asyncio.sleep(0.2)
        return text

    @mcp.tool()
    async def slow_once(text: str) -> str:
        slow_calls.append(1)
        if len(slow_calls) == 1:
            await asyncio.sleep(3)
        return text

    return mcp


@pytest.fixture
def extended_fake(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(mcp_fakes, "build_fake", _build_extended)
    yield


def _text(result: RawResult) -> str:
    return result.parts[0][1]


@asynccontextmanager
async def _http(policy: CountingPolicy) -> AsyncIterator[tuple[FakeServer, SdkConnection]]:
    server = FakeServer("http")
    await server.start()
    try:
        conn = await SdkConnection.open(server.config(), policy)
        try:
            yield server, conn
        finally:
            await conn.close()
    finally:
        await server.stop()


def test_http_server_restarts() -> None:
    policy = CountingPolicy()

    async def run() -> RawResult:
        async with _http(policy) as (server, conn):
            await server.restart()
            return await conn.call_tool("echo", {"text": "after"})

    result = asyncio.run(run())
    assert policy.reconnects == 1
    assert result == RawResult(False, [("text", "after")])


def test_stdio_child_exits() -> None:
    policy = CountingPolicy()

    async def run() -> tuple[int, int, RawResult]:
        conn = await SdkConnection.open(stdio_config(), policy)
        try:
            old_pid = int(_text(await conn.call_tool("pid", {})))
            os.kill(old_pid, signal.SIGKILL)
            result = await conn.call_tool("echo", {"text": "back"})
            new_pid = int(_text(await conn.call_tool("pid", {})))
        finally:
            await conn.close()
        return old_pid, new_pid, result

    old_pid, new_pid, result = asyncio.run(run())
    assert policy.reconnects == 1
    assert result == RawResult(False, [("text", "back")])
    assert new_pid != old_pid


def test_concurrent_failed_calls_reconnect_once() -> None:
    policy = CountingPolicy()

    async def run() -> tuple[int, list[RawResult]]:
        async with _http(policy) as (server, conn):
            before = conn.generation
            await server.restart()
            results = await asyncio.gather(
                conn.call_tool("echo", {"text": "a"}), conn.call_tool("echo", {"text": "b"})
            )
            return conn.generation - before, list(results)

    delta, results = asyncio.run(run())
    assert delta == 1
    assert policy.reconnects == 1
    assert [_text(r) for r in results] == ["a", "b"]


def test_reconnect_fails_then_server_returns() -> None:
    policy = CountingPolicy()

    async def session(server: FakeServer, conn: SdkConnection, step: asyncio.Event) -> list[bool]:
        config = replace(server.config(), tools=["echo"])
        source = McpSource(config, conn, await conn.list_tools())
        await server.stop()
        first = await source.call("echo", {"text": "x"})
        step.set()
        await server.start()  # the same port
        second = await source.call("echo", {"text": "y"})
        return [first.ok, second.ok]

    async def run() -> tuple[list[bool], bool]:
        async with _http(policy) as (server, conn):
            step = asyncio.Event()
            task = asyncio.create_task(session(server, conn, step))
            await step.wait()
            alive_after_failure = not task.done()
            return await task, alive_after_failure

    oks, alive = asyncio.run(run())
    assert oks == [False, True]
    assert alive
    assert policy.reconnects >= 2  # the failed reconnect, then a new one that succeeds


async def _hang(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Accept the socket, read and never answer."""
    while await reader.read(4096):
        pass
    writer.close()


def _switching_factory(urls: list[str]) -> adapters.TransportFactory:
    """Each connect uses the next url in urls; the last one repeats."""

    @asynccontextmanager
    async def factory(config: McpServerConfig, headers: dict[str, str]) -> AsyncIterator[Streams]:
        url = urls.pop(0) if len(urls) > 1 else urls[0]
        async with sdk_transport(replace(config, url=url), headers) as streams:
            yield streams

    return factory


def test_hung_server_during_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "RECONNECT_TIMEOUT", 0.5)

    async def run() -> tuple[float, bool, RawResult]:
        hung_port = free_port()
        hung = await asyncio.start_server(_hang, "127.0.0.1", hung_port)
        server = FakeServer("http")
        await server.start()
        urls = [server.url, f"http://127.0.0.1:{hung_port}/mcp", server.url]
        conn = await SdkConnection.open(
            server.config(), CountingPolicy(), transport_factory=_switching_factory(urls)
        )
        try:
            await server.restart()
            start = time.monotonic()
            with pytest.raises(TimeoutError):
                await conn.call_tool("echo", {"text": "x"})
            elapsed = time.monotonic() - start
            locked = conn._lock.locked()
            result = await conn.call_tool("echo", {"text": "next"})
        finally:
            await conn.close()
            await server.stop()
            hung.close()
        return elapsed, locked, result

    elapsed, locked, result = asyncio.run(run())
    assert elapsed < 0.5 + SLACK
    assert not locked
    assert _text(result) == "next"


def _blocking_once() -> adapters.TransportFactory:
    blocked: list[bool] = []

    @asynccontextmanager
    async def factory(config: McpServerConfig, headers: dict[str, str]) -> AsyncIterator[Streams]:
        first = not blocked
        blocked.append(True)
        async with sdk_transport(config, headers) as streams:
            try:
                yield streams
            finally:
                if first:
                    await asyncio.Event().wait()

    return factory


def test_hung_old_owner_during_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "RECONNECT_TIMEOUT", 0.5)

    async def run() -> tuple[float, bool]:
        async with _http(CountingPolicy()) as (server, _):
            conn = await SdkConnection.open(
                server.config(), CountingPolicy(), transport_factory=_blocking_once()
            )
            old_owner = conn._task
            try:
                start = time.monotonic()
                with pytest.raises(TimeoutError):
                    await conn.reconnect(conn.generation)
                elapsed = time.monotonic() - start
            finally:
                await conn.close()
            return elapsed, old_owner is not None and old_owner.done()

    elapsed, old_done = asyncio.run(run())
    assert elapsed < 0.5 + SLACK
    assert old_done


@pytest.mark.usefixtures("extended_fake")
def test_concurrent_calls_on_one_connection() -> None:
    policy = CountingPolicy()

    async def run() -> list[RawResult]:
        async with _http(policy) as (_, conn):
            results = await asyncio.gather(
                conn.call_tool("wait_echo", {"text": "one"}),
                conn.call_tool("wait_echo", {"text": "two"}),
            )
            return list(results)

    results = asyncio.run(run())
    assert [_text(r) for r in results] == ["one", "two"]
    assert policy.reconnects == 0


@pytest.mark.usefixtures("extended_fake")
def test_call_after_a_timeout() -> None:
    policy = CountingPolicy()

    async def run() -> tuple[float, tuple[bool, str], tuple[bool, str]]:
        async with _http(policy) as (server, conn):
            config = replace(server.config(), timeout=1.0, tools=["slow_once"])
            source = McpSource(config, conn, await conn.list_tools())
            start = time.monotonic()
            first = await source.call("slow_once", {"text": "a"})
            elapsed = time.monotonic() - start
            second = await source.call("slow_once", {"text": "b"})
            return elapsed, (first.ok, first.code or ""), (second.ok, second.text)

    elapsed, first, second = asyncio.run(run())
    assert first == (False, "tool_timeout")
    assert elapsed < 1.5
    assert second == (True, "b")
    assert policy.reconnects == 0


def test_policy_runs_on_reconnect() -> None:
    order: list[str] = []
    policy = CountingPolicy(order)

    def recording(factory: adapters.TransportFactory) -> adapters.TransportFactory:
        @asynccontextmanager
        async def wrapped(
            config: McpServerConfig, headers: dict[str, str]
        ) -> AsyncIterator[Streams]:
            order.append("transport")
            async with factory(config, headers) as streams:
                yield streams

        return wrapped

    async def run() -> None:
        conn = await SdkConnection.open(
            stdio_config(), policy, transport_factory=recording(sdk_transport)
        )
        try:
            await conn.reconnect(conn.generation)
        finally:
            await conn.close()

    asyncio.run(run())
    assert order == ["policy", "transport", "policy", "transport"]


def _slow_first_exit(delay: float) -> adapters.TransportFactory:
    """The first owner's transport exit takes delay seconds; later ones are normal."""
    entered: list[bool] = []

    @asynccontextmanager
    async def factory(config: McpServerConfig, headers: dict[str, str]) -> AsyncIterator[Streams]:
        first = not entered
        entered.append(True)
        async with sdk_transport(config, headers) as streams:
            try:
                yield streams
            finally:
                if first:
                    await asyncio.sleep(delay)

    return factory


def test_close_during_reconnect_leaves_no_owner() -> None:
    policy = CountingPolicy()

    async def run() -> None:
        conn = await SdkConnection.open(
            stdio_config(), policy, transport_factory=_slow_first_exit(0.3)
        )
        job = asyncio.create_task(conn.reconnect(conn.generation))
        await asyncio.sleep(0.1)  # the reconnect is stopping the old owner
        await conn.close()
        await asyncio.gather(job, return_exceptions=True)
        owner = conn._task
        assert owner is not None and owner.done()  # close stopped the newest owner too
        calls = policy.calls
        with pytest.raises(ConnectionClosed):
            await conn.call_tool("echo", {"text": "x"})
        assert policy.calls == calls  # no reconnect after close

    asyncio.run(run())


def test_call_after_close_does_not_reconnect() -> None:
    policy = CountingPolicy()

    async def run() -> None:
        conn = await SdkConnection.open(stdio_config(), policy)
        await conn.close()
        with pytest.raises(ConnectionClosed):
            await conn.call_tool("echo", {"text": "x"})

    asyncio.run(run())
    assert policy.reconnects == 0


def test_session_is_never_visible_with_an_older_generation() -> None:
    """A caller that reads the shared session must read the generation that belongs to it,
    so a failure on a new session is never mistaken for one an earlier reconnect fixed."""

    async def probe(conn: SdkConnection, seen: list[tuple[object, int]]) -> None:
        while True:
            seen.append((conn._session, conn.generation))
            await asyncio.sleep(0)

    async def run() -> tuple[object, list[tuple[object, int]]]:
        conn = await SdkConnection.open(stdio_config(), CountingPolicy())
        first = conn._session
        seen: list[tuple[object, int]] = []
        task = asyncio.create_task(probe(conn, seen))
        try:
            await conn.reconnect(conn.generation)
        finally:
            task.cancel()
            await conn.close()
        return first, seen

    first, seen = asyncio.run(run())
    for session, generation in seen:
        if session is not None:
            assert generation == (0 if session is first else 1)


def test_defaults() -> None:
    assert adapters.RECONNECT_TIMEOUT == 5.0

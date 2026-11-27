"""Issue #77: SdkConnection caps calls in flight and cools down after a failed reconnect."""

import asyncio
from collections.abc import Iterator

import httpx
import mcp_fakes
import pytest
from mcp.server.fastmcp import FastMCP
from mcp_fakes import CountingPolicy, http_connection

from ota_server.mcp import adapters
from ota_server.mcp.config import McpServerConfig
from ota_server.mcp.hooks import ConnectionDenied

# A stopped fake http server: the SDK task group wraps httpx's refused connect.
REFUSED = pytest.RaisesGroup(httpx.ConnectError, flatten_subgroups=True)


class Peak:
    """Counts the wait_echo calls running on the fake server."""

    def __init__(self) -> None:
        self.now = 0
        self.max = 0


@pytest.fixture
def peak(monkeypatch: pytest.MonkeyPatch) -> Iterator[Peak]:
    seen = Peak()

    def build(name: str = "fake") -> FastMCP:
        mcp = FastMCP(name)

        @mcp.tool()
        async def wait_echo(text: str) -> str:
            seen.now += 1
            seen.max = max(seen.max, seen.now)
            try:
                await asyncio.sleep(0.1)
            finally:
                seen.now -= 1
            return text

        return mcp

    monkeypatch.setattr(mcp_fakes, "build_fake", build)
    yield seen


def test_calls_in_flight_never_exceed_the_cap(monkeypatch: pytest.MonkeyPatch, peak: Peak) -> None:
    monkeypatch.setattr(adapters, "MAX_CALLS_IN_FLIGHT", 2)

    async def run() -> list[str]:
        async with http_connection(CountingPolicy()) as (_, conn):
            calls = [conn.call_tool("wait_echo", {"text": str(i)}) for i in range(6)]
            return [r.parts[0][1] for r in await asyncio.gather(*calls)]

    assert asyncio.run(run()) == [str(i) for i in range(6)]
    assert peak.max == 2


def test_cancelled_call_releases_its_slot(monkeypatch: pytest.MonkeyPatch, peak: Peak) -> None:
    monkeypatch.setattr(adapters, "MAX_CALLS_IN_FLIGHT", 1)

    async def run() -> str:
        async with http_connection(CountingPolicy()) as (_, conn):
            slow = asyncio.create_task(conn.call_tool("wait_echo", {"text": "slow"}))
            await asyncio.sleep(0.05)
            slow.cancel()
            with pytest.raises(asyncio.CancelledError):
                await slow
            async with asyncio.timeout(2):
                result = await conn.call_tool("wait_echo", {"text": "next"})
            return result.parts[0][1]

    assert asyncio.run(run()) == "next"


class DenyReconnect(CountingPolicy):
    """Allows the first connect and denies every reconnect."""

    async def before_connect(self, config: McpServerConfig) -> dict[str, str]:
        await super().before_connect(config)
        if self.calls > 1:
            raise ConnectionDenied("denied")
        return {}


def test_denied_reconnect_starts_the_cool_down() -> None:
    policy = DenyReconnect()

    async def run() -> None:
        async with http_connection(policy) as (server, conn):
            await server.stop()
            with pytest.raises(ConnectionDenied):
                await conn.call_tool("echo", {"text": "x"})
            with pytest.raises(ConnectionError, match="cooling down"):
                await conn.call_tool("echo", {"text": "y"})

    asyncio.run(run())
    assert policy.calls == 2


def test_call_within_cool_down_fails_fast() -> None:
    policy = CountingPolicy()

    async def run() -> int:
        async with http_connection(policy) as (server, conn):
            await server.stop()
            with REFUSED:
                await conn.call_tool("echo", {"text": "x"})
            calls = policy.calls
            await server.start()
            with pytest.raises(ConnectionError, match="cooling down"):
                await conn.call_tool("echo", {"text": "y"})
            return policy.calls - calls

    assert asyncio.run(run()) == 0


def test_reconnect_works_after_the_cool_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "RECONNECT_COOLDOWN", 0.2)
    policy = CountingPolicy()

    async def run() -> str:
        async with http_connection(policy) as (server, conn):
            await server.stop()
            with REFUSED:
                await conn.call_tool("echo", {"text": "x"})
            await server.start()
            await asyncio.sleep(0.3)
            result = await conn.call_tool("echo", {"text": "back"})
            return result.parts[0][1]

    assert asyncio.run(run()) == "back"
    assert policy.reconnects == 2


def test_limit_defaults() -> None:
    assert adapters.MAX_CALLS_IN_FLIGHT == 8
    assert adapters.RECONNECT_COOLDOWN == 5.0

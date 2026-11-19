"""McpRegistry (D10): connect all configured MCP servers once at startup, in parallel.

A server that fails or misses the connect deadline is logged and stays off until the server
restarts; there is no background retry (E1). Every connection is closed when the lifespan
AsyncExitStack closes (F4, M4).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AsyncExitStack

from ota_server.mcp.adapters import SdkConnection
from ota_server.mcp.config import McpServerConfig
from ota_server.mcp.hooks import AllowAll, ConnectionPolicy, ToolFilter
from ota_server.mcp.port import McpConnection
from ota_server.mcp.source import McpSource

log = logging.getLogger(__name__)

CONNECT_TIMEOUT = 5.0  # seconds per server connect (D10); read at call time

Connect = Callable[[McpServerConfig, ConnectionPolicy], Awaitable[McpConnection]]
ALLOW_ALL = AllowAll()


class McpRegistry:
    """Builds the shared McpSource list for the app lifespan."""

    @staticmethod
    async def start(
        configs: Sequence[McpServerConfig],
        stack: AsyncExitStack,
        policy: ConnectionPolicy = ALLOW_ALL,
        tool_filter: ToolFilter = ALLOW_ALL,
        connect: Connect = SdkConnection.open,
    ) -> list[McpSource]:
        """Connect all servers in parallel; return the live sources in config order."""
        results = await asyncio.gather(
            *(_start_one(config, stack, policy, tool_filter, connect) for config in configs)
        )
        return [source for source in results if source is not None]


async def _start_one(
    config: McpServerConfig,
    stack: AsyncExitStack,
    policy: ConnectionPolicy,
    tool_filter: ToolFilter,
    connect: Connect,
) -> McpSource | None:
    conn = await _connect(config, stack, policy, connect)
    if conn is None:
        return None
    try:
        source = await McpSource.build(config, conn, tool_filter)
    except Exception as exc:
        log.warning("mcp %s: tool list failed (%s), server off", config.name, type(exc).__name__)
        await _close_quietly(config.name, conn)
        return None
    if not source.specs():
        log.info("mcp %s: no admitted tools, server dropped", config.name)
        await _close_quietly(config.name, conn)
        return None
    return source


async def _connect(
    config: McpServerConfig, stack: AsyncExitStack, policy: ConnectionPolicy, connect: Connect
) -> McpConnection | None:
    """Connect under the deadline and register the close on the stack at once (F4)."""
    try:
        async with asyncio.timeout(CONNECT_TIMEOUT):
            conn = await connect(config, policy)
    except TimeoutError:
        log.warning("mcp %s: no connect within %s s, server off", config.name, CONNECT_TIMEOUT)
        return None
    except Exception as exc:
        # Only the exception type: a message can carry a URL or a header (no secrets in logs).
        log.warning("mcp %s: connect failed (%s), server off", config.name, type(exc).__name__)
        return None
    stack.push_async_callback(_close_quietly, config.name, conn)
    return conn


async def _close_quietly(name: str, conn: McpConnection) -> None:
    """Close one connection; a failure is logged so the other closes still run."""
    try:
        await conn.close()
    except Exception as exc:
        log.warning("mcp %s: close failed (%s)", name, type(exc).__name__)

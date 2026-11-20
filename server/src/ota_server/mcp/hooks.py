"""The access-control seam (D11): a connection policy and a tool filter.

A ConnectionPolicy runs before each connect and may add HTTP headers or deny the server.
A ToolFilter decides which listed tools the LLM sees. The default, ALLOW_ALL, allows all.
"""

from typing import Protocol

from ota_server.mcp.config import McpServerConfig
from ota_server.mcp.port import RawTool


class ConnectionDenied(Exception):
    """Raised by ConnectionPolicy.before_connect to block a server."""


class ConnectionPolicy(Protocol):
    async def before_connect(self, config: McpServerConfig) -> dict[str, str]:
        """Return extra HTTP headers, or raise ConnectionDenied to block the server."""
        ...


class ToolFilter(Protocol):
    def admit(self, server: str, tool: RawTool) -> bool: ...


class AllowAll:
    """The default ConnectionPolicy and ToolFilter: no extra headers, every tool admitted."""

    async def before_connect(self, config: McpServerConfig) -> dict[str, str]:
        return {}

    def admit(self, server: str, tool: RawTool) -> bool:
        return True


ALLOW_ALL = AllowAll()  # the shared default policy and filter; it holds no state

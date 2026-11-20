"""The SDK-free connection port (D2): the contract between McpSource and the adapters."""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class RawTool:
    """One tool as the MCP server lists it."""

    name: str
    description: str
    schema: dict[str, Any]


@dataclass(frozen=True)
class RawResult:
    """One call result. parts holds (kind, text); kind is "text", "image" or "resource",
    and text is "" for a non-text part."""

    is_error: bool
    parts: list[tuple[str, str]]


class McpConnection(Protocol):
    async def list_tools(self) -> list[RawTool]: ...

    async def call_tool(self, name: str, args: dict[str, Any]) -> RawResult: ...

    async def close(self) -> None:
        """Close the connection. Must be idempotent: a second call is a no-op. The registry
        may close a dropped server early and the lifespan stack closes it again."""
        ...

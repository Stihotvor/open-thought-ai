"""McpSource (D5): the ToolSource for one external MCP server. All policy, no SDK code.

The hub truncates results in wrap() and guards each call at timeout + GUARD_GRACE, so this
source does not truncate and times out first (D6).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ota_server.hub.types import TOOL_NAME, ToolOutcome, ToolSpec
from ota_server.mcp.config import McpServerConfig
from ota_server.mcp.hooks import ALLOW_ALL, ToolFilter
from ota_server.mcp.port import McpConnection, RawResult, RawTool

log = logging.getLogger(__name__)

PLACEHOLDERS = {"image": "[image]", "resource": "[resource]"}


class McpSource:
    """One external server as a ToolSource: allowlist, naming, result mapping, timeout."""

    def __init__(
        self,
        config: McpServerConfig,
        conn: McpConnection,
        tools: list[RawTool],
        tool_filter: ToolFilter = ALLOW_ALL,
    ) -> None:
        self.name = config.name
        self.timeout = config.timeout
        self._conn = conn
        self._specs = _build_specs(config, tools, tool_filter)
        self._kept = {spec.name for spec in self._specs}

    @classmethod
    async def build(
        cls, config: McpServerConfig, conn: McpConnection, tool_filter: ToolFilter = ALLOW_ALL
    ) -> McpSource:
        """List the server's tools once and build the source."""
        return cls(config, conn, await conn.list_tools(), tool_filter)

    def specs(self) -> list[ToolSpec]:
        return list(self._specs)

    async def call(self, tool: str, args: dict[str, Any]) -> ToolOutcome:
        """Run one tool; never raises except CancelledError (D5a)."""
        if tool not in self._kept:
            return ToolOutcome(False, "unknown tool", "unknown_tool")
        try:
            async with asyncio.timeout(self.timeout):
                result = await self._conn.call_tool(tool, args)
        except TimeoutError:
            return ToolOutcome(False, f"the {self.name} did not answer in time", "tool_timeout")
        except Exception as exc:
            # Only the type: the message and traceback can carry server-controlled text.
            log.warning("mcp %s: tool %r failed (%s)", self.name, tool, type(exc).__name__)
            return ToolOutcome(False, "tool failed", "mcp_error")
        return _map_result(result)


def _build_specs(
    config: McpServerConfig, tools: list[RawTool], tool_filter: ToolFilter
) -> list[ToolSpec]:
    listed = {tool.name for tool in tools}
    for name in config.tools:
        if name not in listed:
            log.warning(
                "mcp %s: allowlisted tool %r is not on the server, ignored", config.name, name
            )
    allowed = set(config.tools)
    specs: list[ToolSpec] = []
    for tool in tools:
        if tool.name in allowed and tool_filter.admit(config.name, tool):
            spec = _to_spec(config.name, tool)
            if spec is not None:
                specs.append(spec)
    return specs


def _to_spec(server: str, tool: RawTool) -> ToolSpec | None:
    spec = ToolSpec(source=server, name=tool.name, description=tool.description, schema=tool.schema)
    if not TOOL_NAME.fullmatch(spec.full_name):
        log.error("mcp %s: tool %r dropped, bad full name %r", server, tool.name, spec.full_name)
        return None
    return spec


def _map_result(result: RawResult) -> ToolOutcome:
    """E4: no raw error body. E5: placeholders for non-text parts. No truncation."""
    if result.is_error:
        return ToolOutcome(False, "the tool reported an error", "tool_error")
    text = "\n".join(
        body if kind == "text" else PLACEHOLDERS.get(kind, f"[{kind}]")
        for kind, body in result.parts
    )
    return ToolOutcome(True, text)

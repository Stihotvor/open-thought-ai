"""McpHub: lists the tools of all sources (D2) and runs each call through the chain (D3)."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from typing import Any

from ota_server.hub.chain import build_chain
from ota_server.hub.steps import limit, resolve, validate, wrap
from ota_server.hub.types import SEP, CallCtx, ToolCall, ToolOutcome, ToolSource, ToolSpec

log = logging.getLogger(__name__)

GUARD_GRACE = 0.5  # seconds past source.timeout: the source's own timeout fires first
TOOL_NAME = re.compile(r"[a-zA-Z0-9_-]{1,64}")  # the full name the LLM API accepts (D13)


def _check_names(source: ToolSource) -> None:
    """Raise ValueError for a source name with SEP or a full tool name the LLM API refuses."""
    if SEP in source.name:
        raise ValueError(f"tool source name {source.name!r} contains {SEP!r}")
    for spec in source.specs():
        if not TOOL_NAME.fullmatch(spec.full_name):
            raise ValueError(f"bad tool name {spec.full_name!r}")


class TurnTools:
    """The ToolContext of one turn: one CallCtx shared by all its calls."""

    def __init__(self, hub: McpHub, ctx: CallCtx) -> None:
        self._hub = hub
        self._ctx = ctx

    def list_tools(self) -> list[dict[str, Any]]:
        return self._hub.list_tools()

    async def run(self, call: ToolCall) -> ToolOutcome:
        return await self._hub.run(call, self._ctx)


class McpHub:
    def __init__(self, sources: Sequence[ToolSource]) -> None:
        for source in sources:
            _check_names(source)
        self._sources = {source.name: source for source in sources}
        self._specs: dict[str, ToolSpec] = {
            spec.full_name: spec for source in sources for spec in source.specs()
        }
        self._chain = build_chain(
            [wrap(), resolve(self._specs), limit(), validate(self._specs)], self._terminal
        )

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.full_name,
                    "description": spec.description,
                    "parameters": spec.schema,
                },
            }
            for spec in self._specs.values()
        ]

    async def run(self, call: ToolCall, ctx: CallCtx) -> ToolOutcome:
        """Run one call through the chain; never raises (except on cancel)."""
        try:
            outcome = await self._chain(call, ctx)
        except Exception:
            log.exception("tool call %s failed", call.name)
            outcome = ToolOutcome(False, "tool failed", "internal")
        log.info("tool call %s ok=%s code=%s", call.name, outcome.ok, outcome.code)
        return outcome

    def turn_context(self) -> TurnTools:
        return TurnTools(self, CallCtx())

    async def _terminal(self, call: ToolCall, ctx: CallCtx) -> ToolOutcome:
        source_name, _, tool = call.name.partition(SEP)
        args: dict[str, Any] = call.args if isinstance(call.args, dict) else {}
        source = self._sources[source_name]
        try:
            async with asyncio.timeout(source.timeout + GUARD_GRACE):  # outer guard (D4a)
                return await source.call(tool, args)
        except TimeoutError:
            log.warning("tool %s timed out (outer guard)", call.name)
            return ToolOutcome(False, f"the {source.name} did not answer in time", "tool_timeout")

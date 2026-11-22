"""McpHub: lists the tools of all sources (D2) and runs each call through the chain (D3)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from ota_server.hub.chain import build_chain
from ota_server.hub.steps import limit, resolve, validate, wrap
from ota_server.hub.types import (
    SEP,
    TOOL_NAME,
    CallCtx,
    ToolCall,
    ToolOutcome,
    ToolSource,
    ToolSpec,
    source_name_problem,
)

log = logging.getLogger(__name__)

GUARD_GRACE = 0.5  # seconds past source.timeout: the source's own timeout fires first


def _keep_sources(sources: Sequence[ToolSource]) -> dict[str, ToolSource]:
    """Keep the first source per name; skip later ones and names with SEP (D7)."""
    kept: dict[str, ToolSource] = {}
    for source in sources:
        problem = source_name_problem(source.name, kept)
        if problem is not None:
            log.error("tool source %r skipped: %s", source.name, problem)
        else:
            kept[source.name] = source
    return kept


def _keep_specs(sources: dict[str, ToolSource]) -> dict[str, ToolSpec]:
    """Keep the first spec per full name; skip later ones and bad names (D7).

    Log lines carry names only, never tool descriptions.
    """
    kept: dict[str, ToolSpec] = {}
    for source in sources.values():
        for spec in source.specs():
            name = spec.full_name
            if not TOOL_NAME.fullmatch(name):
                log.error("tool %r skipped: bad tool name", name)
            elif name in kept:
                log.error("tool %r skipped: the name is already used", name)
            else:
                kept[name] = spec
    return kept


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
    def __init__(self, sources: Sequence[ToolSource], language: str = "en") -> None:
        self._language = language  # the session language (D5)
        self._sources = _keep_sources(sources)
        self._specs = _keep_specs(self._sources)
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

    def turn_context(self, turn: int = 0) -> TurnTools:
        return TurnTools(self, CallCtx(turn=turn))

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

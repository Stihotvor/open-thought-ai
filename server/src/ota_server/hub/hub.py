"""McpHub: lists the tools of all sources (D2) and runs each call through the chain (D3)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from ota_server.hub import confirm_text
from ota_server.hub.chain import build_chain
from ota_server.hub.confirm import ConfirmGate, Held
from ota_server.hub.steps import confirm, limit, resolve, validate, wrap
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


def _text(texts: dict[str, str], language: str) -> str:
    """The fixed text for `language`; English for an unknown language, so it never raises."""
    return texts.get(language, texts["en"])


class TurnTools:
    """The ToolContext of one turn: one CallCtx shared by all its calls."""

    def __init__(self, hub: McpHub, ctx: CallCtx) -> None:
        self._hub = hub
        self._ctx = ctx

    def list_tools(self) -> list[dict[str, Any]]:
        return self._hub.list_tools()

    async def run(self, call: ToolCall) -> ToolOutcome:
        return await self._hub.run(call, self._ctx)

    def held_question(self) -> str | None:
        """The fixed question when this turn holds a call in phase held, else None (D7)."""
        held = self._hub.gate.holding()
        if held is None or held.turn != self._ctx.turn:
            return None
        source, _, tool = held.name.partition(SEP)
        language = self._hub._language
        return confirm_text.question(
            language if language in confirm_text.QUESTION else "en", source, tool
        )

    async def confirmation(self, text: str) -> str | None:
        """Answer a call claimed by this turn: run it once on a yes, else cancel (D6).

        None when no call is claimed by this turn. Never raises, except on cancel (F4).
        """
        gate, language = self._hub.gate, self._hub._language
        if not gate.claimed_by(self._ctx.turn):
            return None
        try:
            held = gate.pop()  # gone before the run: a second yes runs nothing (T3)
            if held is None or not confirm_text.is_yes(text, language):
                return _text(confirm_text.CANCELLED, language)
            outcome = await self._hub._release(held)
            return _text(confirm_text.DONE if outcome.ok else confirm_text.FAILED, language)
        except Exception:
            log.exception("confirmation failed; the held call is dropped")
            gate.drop()
            return _text(confirm_text.CANCELLED, language)


class McpHub:
    def __init__(self, sources: Sequence[ToolSource], language: str = "en") -> None:
        self._language = language  # the session language (D5)
        self._sources = _keep_sources(sources)
        self._specs = _keep_specs(self._sources)
        self._gate = ConfirmGate()  # one held call per session (D2)
        steps = [
            wrap(),
            resolve(self._specs),
            limit(),
            validate(self._specs),
            confirm(self._specs, self._gate),
        ]
        self._chain = build_chain(steps, self._terminal)
        self._release_chain = build_chain([wrap()], self._terminal)  # no limit (E9)

    @property
    def gate(self) -> ConfirmGate:
        """The session's confirm gate; the session arms it (D9)."""
        return self._gate

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

    async def _release(self, held: Held) -> ToolOutcome:
        """Run a confirmed call: wrap and terminal only. Never raises, except on cancel."""
        call = ToolCall(held.call_id, held.name, held.args)
        try:
            outcome = await self._release_chain(call, CallCtx(turn=held.turn))
        except Exception:
            log.exception("released tool call %s failed", held.name)
            outcome = ToolOutcome(False, "tool failed", "internal")
        log.info("released tool call %s ok=%s code=%s", held.name, outcome.ok, outcome.code)
        return outcome

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

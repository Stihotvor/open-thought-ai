"""Hub types: the contract shared by the chain, the sources and the pipeline."""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

MAX_TOOL_CALLS = 4  # per turn
MAX_RESULT_CHARS = 2000  # per result sent to the LLM
SEP = "__"  # full tool name = f"{source}{SEP}{tool}"
TOOL_NAME = re.compile(r"[a-zA-Z0-9_-]{1,64}")  # the full name the LLM API accepts (D13)


@dataclass(frozen=True)
class ToolCall:
    """One call asked by the LLM; args is None when its JSON was broken."""

    id: str
    name: str
    args: object


@dataclass(frozen=True)
class ToolOutcome:
    """Result of one call; text goes to the LLM as the tool message content."""

    ok: bool
    text: str
    code: str | None = None


@dataclass
class CallCtx:
    """One per turn; counts calls across all rounds."""

    calls: int = 0
    max_calls: int = MAX_TOOL_CALLS


@dataclass(frozen=True)
class ToolSpec:
    source: str
    name: str
    description: str
    schema: dict[str, Any]
    confirm: bool = False

    @property
    def full_name(self) -> str:
        return f"{self.source}{SEP}{self.name}"


class ToolSource(Protocol):
    name: str
    timeout: float  # seconds; the hub's outer guard per call (D4a)

    def specs(self) -> list[ToolSpec]: ...

    async def call(self, tool: str, args: dict[str, Any]) -> ToolOutcome:
        """Run the bare tool name; never raises."""
        ...


class ToolContext(Protocol):
    """What the pipeline sees."""

    def list_tools(self) -> list[dict[str, Any]]: ...

    async def run(self, call: ToolCall) -> ToolOutcome:
        """Run one call through the chain; never raises."""
        ...


Next = Callable[[ToolCall, CallCtx], Awaitable[ToolOutcome]]
Step = Callable[[ToolCall, CallCtx, Next], Awaitable[ToolOutcome]]

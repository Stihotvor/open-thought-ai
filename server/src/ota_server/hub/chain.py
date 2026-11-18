"""Build one callable from a list of steps; the first step is the outermost."""

from collections.abc import Sequence

from ota_server.hub.types import CallCtx, Next, Step, ToolCall, ToolOutcome


def _link(step: Step, next_: Next) -> Next:
    async def run(call: ToolCall, ctx: CallCtx) -> ToolOutcome:
        return await step(call, ctx, next_)

    return run


def build_chain(steps: Sequence[Step], terminal: Next) -> Next:
    next_ = terminal
    for step in reversed(steps):
        next_ = _link(step, next_)
    return next_

# Path 3 — Middleware chain (chain of responsibility) in the hub

Core idea: every tool call runs through an ordered chain of async steps: resolve → limit → validate → [confirm, #30] → execute → wrap. #30/#31 insert steps. #28 registers sources and a terminal handler.

## Components
- `hub/types.py`: `ToolSpec`, `ToolCall`, `ToolOutcome`, `CallCtx`, `Step`, `Handler`.
- `hub/chain.py`: `build_chain(steps, terminal) -> Handler`.
- `hub/steps.py`: `resolve`, `limit`, `validate`, `wrap`.
- `hub/hub.py`: `McpHub.list_tools`, `McpHub.run(call, ctx)` (never raises).
- `hub/device_source.py`: fixed table and `DeviceExecutor` terminal step (sends `tool.call`, ≤ 1024 bytes, awaits 2 s).
- `hub/pending.py`: `PendingCalls` (`expect`, `resolve`, `cancel_all`).
- `providers.py`: `chat_turn(...)` yields `ChatEvent`. `chat()` is built on it, unchanged signature.
- `pipeline.py`: tool rounds in `read_chat`, `hub=None` keeps old paths. `session.py`: owns `PendingCalls`, routes `tool.result`.

## Failure modes
- F1: `execute` catches the timeout → `tool_timeout`. F2: error outcome. F3: `cancel_all("device_disconnected")`.
- F4: `ProviderError` → `llm_failed`. F5: `wrap` step.

Effort: M. Main risk: tool rounds vs per-sentence streaming and `TurnBudget` (2 s waits must not eat the turn deadline).

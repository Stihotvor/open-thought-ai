# Path 4 — Minimal monolith (one `tools.py`, refactor at #28)

Core idea: least new code. One module `tools.py` holds the static device tool table and `DeviceTools`. The tool loop sits in `VoicePipeline.read_chat`. `DeviceSession` owns the pending futures. A narrow `ToolHub` protocol (`specs`, `run`) is the only seam. #28 swaps in a real hub.

## Components
- `tools.py` (~150 lines): `TOOLS` table, `ToolHub` protocol, `DeviceTools` (validate, cap, truncate, wrap), `ToolCall`.
- `providers.py`: `chat(..., tools=None)` yields `str | ToolCall`.
- `pipeline.py`: tool loop in `read_chat`, max 3 rounds.
- `session.py`: `pending: dict[str, Future]`, `call_tool(name, args, timeout=2.0)`, `on_tool_result`.
- `protocol.py`: `tool.result` shape check. `reply.py`: `CallTool` type.

## Failure modes
- F1: `wait_for(2.0)` → `tool_timeout`. F2: error text passed on. F3: session `finally` fails all futures.
- F4: `ProviderError` → `llm_failed`. F5: truncation in `DeviceTools`.

Effort: M (smallest of the four). Main risk: the seam passes a session-specific `DeviceSend` into `run`, which leaks into #28 (external MCP calls do not use the socket). Rules live in the device class, so #28 must move them.

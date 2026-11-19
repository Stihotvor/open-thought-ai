# Path 1 — Hexagonal hub with ToolSource plugins

Core idea: the pipeline depends on one port, `McpHub`. The hub owns the shared rules (namespacing, lookup, schema check, 4-call cap, 2 s timeout, truncation, untrusted wrapper) and delegates execution to `ToolSource` adapters. `DeviceToolSource` is the first adapter. #28 adds an external MCP source. #30/#31 fill a `Policy` hook.

## Components
- `mcp/ports.py`: `ToolSpec`, `ToolResult`, `ToolSource` protocol.
- `mcp/hub.py`: `McpHub.register`, `openai_tools`, `run_turn`, `cancel_all`.
- `mcp/device.py`: `DeviceToolSource` with the fixed specs, sends `tool.call`, `{id: Future}` table, `on_result`.
- `mcp/policy.py`: `Policy.check(spec, args) -> Allow | Deny | Confirm`. Allow-all in #27.
- `providers.py`: `chat_turn(messages, tools, budget)` yields `TextDelta | ToolCallsDelta`. `chat` stays.
- `pipeline.py`: `read_chat` loops chat → hub → chat, max 3 rounds. Echo pipeline gets no hub (E8).
- `session.py`: builds the hub per connection, routes `tool.result`, calls `cancel_all` on abort and close.

## Failure modes
- F1: hub-level `asyncio.timeout(2.0)` returns `tool_timeout` to the LLM. Same rule for #28 sources.
- F2: `ok:false` is a normal error result. F3: `close()` fails all pending Futures.
- F4: broken args JSON or 4xx → `ProviderError` → `llm_failed`. F5: truncate to 2000 chars.

Effort: M. Main risk: the tool loop can break per-sentence streaming and `TurnClock`. Two SSE parsers must share one core. Tool messages stay out of `History` (assumption).

# Decision — device tools over `tool.call`/`tool.result` (issue #27)

## Context
The LLM must call the fixed device tools `set_volume`, `set_face` and `get_status` (`docs/protocol.md`). The server sends `tool.call` and the device answers with `tool.result` within 2 s. No hub code exists today. `session.py` ignores `tool.result`, and `ModelClient.chat` sends no `tools`. Issues #28–#31 add external MCP sources, the n8n example, `confirm` and safety checks to the same hub. Requirements: `requirements.md`.

## Options
- Path 1: hexagonal hub with `ToolSource` adapters and a `Policy` hook. Feasible with changes (the timeout must be per source).
- Path 2: event bus, pump task and id correlator. Feasible with changes (4 moving parts, 1 follow-up round only).
- Path 3: middleware chain in the hub: resolve → limit → validate → [confirm] → execute → wrap. Feasible.
- Path 4: minimal `tools.py`, refactor at #28. Feasible with changes (the seam fails B2).

## Decision
The user chose Path 3 at G1 on 2026-11-18. The design takes these reviewer changes:
- D1: Package `server/src/ota_server/hub/`: `types.py`, `chain.py`, `steps.py`, `hub.py`, `device_source.py`, `pending.py`.
- D2: `McpHub.list_tools()` returns OpenAI `tools` entries named `device__<tool>`. `McpHub.run(call, ctx) -> ToolOutcome` never raises.
- D3: Chain order for #27: `resolve`, `limit`, `validate`, then the source's terminal step, then `wrap`. #30 inserts `confirm` before the terminal step. #31 adds safety steps. `build_chain(steps, terminal)` takes a list.
- D4: The timeout is a property of each tool source. The device source uses 2.0 s and returns a `tool_timeout` error outcome on expiry.
- D5: `CallCtx` is per turn. It holds the call counter, so the 4-call cap (T5) counts across all rounds of one turn.
- D6: `DeviceSession` owns `PendingCalls` (id → Future). `handle_text` routes `tool.result` in every state. Each waiting call removes its id in a `finally` block, so abort (E6) and close (F3) leave no entry. A late or unknown id is logged and ignored (E3). Call ids are unique for the whole session (a counter, not a per-turn number), so a late result from a cancelled turn cannot match a call of the next turn. A new `DeviceSession.close()` fails every waiting future. The `finally` block in `gateway.py` calls it when the socket closes (F3).
- D7: `reply.ReplySource` gets a keyword argument `tools: ToolContext | None = None`, next to `notify`. The default keeps `echo` and the existing test fakes valid (E8, M7). `VoicePipeline` is one object for the app, so it stores no hub. The session builds the `McpHub` and its device source for each turn and passes it in this argument. `hub/` must not import `session.py` or `pipeline.py`. `ToolContext` lives in `hub/types.py`. The device source gets a small send function and the `PendingCalls` object, not the `DeviceSession` object.
- D8: `providers.py` adds `chat_turn(messages, budget, tools, tool_choice=None)` that yields `TextDelta | ToolCallsDone`. It joins streamed `tool_calls` deltas by index. `chat()` keeps its signature and is built on `chat_turn`, so existing callers and tests do not change. A stream with more than 8 tool-call indexes, or more than 4096 characters of joined arguments, raises `ProviderError(CHAT)` (`llm_failed`), so memory stays bounded.
- D9: `read_chat` runs at most 3 tool rounds and then 1 final text round, which fits `CALL_CAPS[CHAT]=4`. Tool messages stay out of `History` and out of moderation input. Tool results go to the LLM wrapped as untrusted data and truncated to 2000 characters.
- D10: The turn budget is extended by the time spent in each tool wait. The wait happens before the call that needs the time, so the next chat call gets the extension. A wait can overlap playback, which `speak()` also extends. This double extension is accepted: it is at most 4 × 2 s = 8 s per turn. A TTS call that is already in progress keeps its fixed timeout.
- D11: Text that a model streams before `tool_calls` is spoken as usual. It passes the per-sentence moderation and TTS path that exists today. At the end of each tool round, `read_chat` flushes an unfinished half sentence as one sentence, so no text is lost before the next round.
- D12: `set_face.face` is an enum of the faces in `firmware/main/face.h`: `idle`, `listening`, `thinking`, `speaking`, `offline`, `error`. With this enum each `tool.call` frame stays ≤ 1024 bytes. The device source checks the serialized frame size before it sends.

## Consequences
- The pipeline sees only `hub.list_tools()` and `hub.run()`. #28–#31 add sources and steps without pipeline edits.
- New code: one package of about 6 small modules plus edits to `providers.py`, `pipeline.py`, `session.py`, `reply.py` and `gateway.py`. Effort M.
- Risk: tool rounds touch per-sentence streaming and `TurnClock` timing. Tests must cover a text-only turn on the same path.
- Risk: a turn with 4 chat rounds, tool waits and 429 retries can pass the 8 s deadline and end with `llm_failed`. A text-only turn uses 1 chat round, as today. The 3-round cap keeps this bounded.
- Accepted: a slow device is not retried. One fixed 2 s limit applies to every device tool.
- No change to protocol v1 and no new paid service. Tests use a fake chat endpoint, a fake socket and fake tools only.

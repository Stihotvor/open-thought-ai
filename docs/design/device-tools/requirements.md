# Requirements — device tools over `tool.call`/`tool.result` (issue #27)

First issue of milestone M4 (MCP hub). Issues #28–#31 add the external MCP client, the n8n example, the `confirm` flow and the tool safety rules. This issue builds the hub seam they plug into, but not their work.

## Business Goals
- B1: The user can say "set the volume to 30" or "how is the device" and the device acts or answers. The LLM calls a device tool, the server sends `tool.call`, and the device answers with `tool.result`.
- B2: The MCP hub exists as one place that lists tools and runs calls. #28 adds external MCP tool sources and #30/#31 add `confirm` and safety checks to the same hub, with no change to the pipeline.
- B3: Close the first item of M4, so the M4 exit check ("Device tool + one n8n MCP tool work, risky tool asks to confirm") can build on it.

## Technical Constraints
- T1: Python 3.12 FastAPI server in `server/` with ruff and strict mypy. Budget 0 €. No new paid service.
- T2: Protocol v1 does not change. `tool.call` S→D `{id, name, args}`, `tool.result` D→S `{id, ok, result | error}`, handled in any state. The result must arrive within 2 s, else the server records `tool_timeout` (`docs/protocol.md`).
- T3: The device tools are fixed on the server: `set_volume {level 0..100}`, `set_face {face}`, `get_status {}`. The device cannot declare tools (`docs/protocol.md`, `docs/security.md`).
- T4: The LLM calls tools through the OpenAI-compatible `tools` field of `/chat/completions` (streamed `tool_calls` deltas). The chat endpoint is per role, like today (`providers.py`).
- T5: At most 4 tool calls per turn (`docs/security.md`). A call over the cap is not run.
- T6: Tool args are validated against the fixed JSON Schema before `tool.call` goes out. Results go back to the LLM wrapped as untrusted data (`docs/security.md`).
- T7: Device tools are enabled and have `confirm=false`. The `confirm` flow is #30 scope. Tool names in the hub are namespaced `device__<tool>` (`<server>__<tool>`, `docs/security.md`).
- T8: Text frames stay at most 1024 bytes (`protocol.parse_text`). An outgoing `tool.call` must fit in this limit.
- T9: Tests use fake tools, a fake chat endpoint and a fake socket only. No board and no real API call.

## Edge Cases
- E1: The LLM asks for an unknown tool name: the hub returns an error result to the LLM. No `tool.call` goes to the device.
- E2: The LLM sends args that fail the schema (for example `level: 150` or a missing field): the hub returns an error result to the LLM. No `tool.call` goes to the device.
- E3: The device sends `tool.result` with an unknown or already-answered `id`: the server ignores it and logs it. The session survives.
- E4: The device sends a malformed `tool.result` (no `ok`, wrong types): the server treats it as an error result for that id, or ignores it if the id is unknown.
- E5: The LLM asks for 5 or more tool calls in one turn: calls 1–4 run, the rest get an error result "tool call limit reached".
- E6: The user aborts (`abort`) while a `tool.call` waits: the wait is cancelled, no late `tool.result` changes the next turn.
- E7: A tool call with no text reply (for example `set_face`): the LLM gets the result and makes a short spoken reply as usual.
- E8: The echo pipeline (`OTA_PIPELINE` default) has no LLM and no tools. It keeps working unchanged.

## Failure Modes
- F1: No `tool.result` within 2 s: the hub returns a `tool_timeout` error result to the LLM, and the turn goes on. The LLM can say that the device did not answer.
- F2: The device answers `ok: false` with an error: the LLM gets the error text as the result. The turn does not fail.
- F3: The socket closes while a call waits: every waiting call ends with an error. No task leaks.
- F4: The chat model returns no tool support or a broken `tool_calls` delta: the turn ends with `llm_failed`, like any bad chat stream.
- F5: A tool result is very large: the hub truncates it before it goes to the LLM, so the chat request stays bounded.

## Success Metrics
- M1: In a fake turn where the LLM calls `device__set_volume {level: 30}`, the device socket gets exactly 1 `tool.call {name: "set_volume", args: {level: 30}}`. After the fake device answers, the LLM gets the result and the user hears the final reply. The test passes with 0 real API calls.
- M2: With no `tool.result`, the call ends in 2.0 s ± 0.2 s with a `tool_timeout` error result, and the turn finishes.
- M3: With 6 tool calls requested in one turn, exactly 4 `tool.call` messages go out.
- M4: For each of 3 invalid-args cases and 1 unknown-tool case, 0 `tool.call` messages go out.
- M5: A `tool.result` with an unknown id leaves the session open, and the next turn works.
- M6: Each `tool.call` frame is ≤ 1024 bytes. Each tool result sent to the LLM is ≤ 2000 characters.
- M7: All existing server tests stay green. ruff and mypy report 0 errors. New code has ≥ 90 % line coverage.

# Requirements — confirm flow (issue #30)

Fifth issue of milestone M4 (MCP hub). #27 built the hub and the device source, #28 the external MCP client, #31 the tool safety. This issue adds the `confirm` flow of `docs/protocol.md:67-71` and `docs/security.md` ("Confirmation is enforced by the hub, bound to the tool name, args and turn, single use"). Out of scope: #29 (n8n example), the web UI switch for `confirm`, and the firmware side of `confirm.request`.

The owner delegated all choices in this stage ("Architect, dba and other agents do a decision"). The grill-me answers below come from the code and the docs. Where a product choice remained, the safest option was picked and is marked `ASSUMPTION:`.

## Business Goals
- B1: A risky tool (a tool that changes something outside the server) runs only after the user says "yes" by voice. The LLM cannot run it alone, also when a tool result or a prompt injection tells it to.
- B2: The flow works the same for device tools and for external MCP tools, so #29 (n8n) can add a risky tool with config only.
- B3: The flow is the last M4 safety control before the owner tests in M6.

## Technical Constraints
- T1: Python 3.12 FastAPI server in `server/`. ruff, strict mypy, pytest, coverage >= 90 % (`server/scripts/gate.sh`). Budget 0 €. No new dependency.
- T2: Enforcement is in the hub (`hub/`), as a chain step after `validate` and before the terminal step (device-tools D3). The pipeline and the LLM cannot skip it. The hub is one object per session, so the held call outlives the turn (device-tools D7a).
- T3: The held call is bound to the full tool name, the exact validated args and the turn id. It is single use: it runs at most once, and then it is gone.
- T4: Confirm flags.
  - MCP tools: `confirm=true` by default (`docs/security.md`: new tools start `confirm=true`). A servers file entry may turn it off per tool with a new optional field `no_confirm: list[str]` of bare tool names. A name in `no_confirm` that is not a key of `tools` rejects the entry. ASSUMPTION: opt-out list, not opt-in, so a new tool fails closed.
  - Device tools: the flag is fixed in code. ASSUMPTION: `set_volume` gets `confirm=true` (it changes the device state audibly). `get_status` (read only) and `set_face` (screen only, no lasting effect) keep `confirm=false`.
- T5: Protocol (`docs/protocol.md`). When a call is held, the turn reply speaks a short fixed question. After `reply.end` the server sends `state {state: confirm}` and `confirm.request {turn, tool, timeout_ms: 10000}`. The 10 s timer starts after `reply.end`.
- T6: The question is a fixed server text per language (EN, PL, UK) with the tool name. The LLM does not write it. ASSUMPTION: a fixed text cannot hide or change what runs.
- T7: Classification has no LLM. The transcript is lowercased, its punctuation and spaces at the ends are stripped, and the whole result must equal one yes word of the session language: EN `yes`, `yeah`; PL `tak`; UK `так`. ASSUMPTION: an exact match of the whole transcript only. "Yes, but not now" is not a yes.
- T8: After a yes, the held call runs once through the source, with no new LLM chat. The server speaks a fixed short result text per language ("Done." or "That did not work."). ASSUMPTION: no LLM round after the run, so a tool result cannot steer a new tool call in the confirmation turn.
- T9: Anything other than a yes cancels the held call. The confirmation turn then ends with a fixed "Cancelled." text and does not go to the LLM. ASSUMPTION: the smallest scope.
- T10: A timeout cancels the held call and sends `error {turn, code: confirm_timeout}` with the turn of the `confirm.request`.
- T11: Tests use fake sources (a fake device socket and the #28 fake MCP servers or a fake `ToolSource`). No board, no external service.

## Edge Cases
- E1: The LLM asks two `confirm` tools in one turn: the first is held. Every later `confirm` call in that turn gets the outcome `confirm_busy` and does not run. ASSUMPTION: one held call per session.
- E2: A held call exists and a new turn starts before the timeout: that turn is the confirmation turn (T7), also when the user asks something else.
- E3: The user presses the button (`abort`) during the question: the held call is cancelled, and the timer does not start.
- E4: The socket closes while a call is held: the held call is dropped, never run.
- E5: A `listen.start` arrives after the timeout: the held call is already gone, the turn is a normal turn.
- E6: The STT finds no speech (`no_speech`) in the confirmation turn: the held call is cancelled (not a yes).
- E7: A `too_many_turns` refusal of the confirmation turn: the held call stays until the timeout. A refused turn is not a turn.
- E8: A non-`confirm` call in the same turn as a held call runs normally (E1 limits only `confirm` calls).
- E9: The held call counts against the cap of 4 calls of its own turn. The run after the yes does not count again.

## Failure Modes
- F1: The source fails or times out after the yes: the user hears the fixed failure text. The session survives, and `McpHub.run` never raises.
- F2: The MCP server of the held call disconnected before the yes: the run returns an error outcome (F1).
- F3: The LLM gets the outcome `confirm_required` ("held: the user must say yes") for the held call, never a fake success. It cannot claim that the tool ran.
- F4: A bug in the confirm step logs an error and treats the call as not confirmed (fail closed).
- F5: A moderation refusal or an STT failure in the confirmation turn cancels the held call.

## Success Metrics
- M1: A fake `confirm` tool is not run in its own turn: 0 calls on the fake source before the confirmation turn, in a test for a device tool and a test for an MCP tool.
- M2: Yes words: 4 of 4 (`yes`, `yeah`, `tak`, `так`) run the held call exactly 1 time. A second yes turn runs it 0 more times.
- M3: Non-yes inputs (`no`, `yes but later`, empty transcript, a yes word of another language) cancel: 4 of 4 cases with 0 calls.
- M4: Timeout: with a fake clock, 10 s after `reply.end` the device gets `error {code: confirm_timeout}`, and a later yes runs 0 calls.
- M5: Message order in a session test: `reply.end`, then `state confirm`, then `confirm.request {timeout_ms: 10000}`.
- M6: Config tests: an unknown name in `no_confirm` rejects the entry. Default MCP specs have `confirm=true`. 2 of 2 pass.
- M7: `server/scripts/gate.sh` passes: ruff 0 findings, mypy 0 errors, all tests pass, coverage >= 90 %.

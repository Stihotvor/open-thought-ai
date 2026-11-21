# Decision — confirm flow (issue #30)

## Context
The hub must hold a tool call flagged `confirm` until the user says "yes" by voice (`docs/protocol.md:67-71`, `docs/security.md`). Today `ToolSpec.confirm` exists but no source sets it, and the chain is wrap, resolve, limit, validate. The hub is one object per session (device-tools D7a), so a held call can outlive its turn. Requirements: `requirements.md`.

## Options
- Path 1: hub-owned gate. A chain step and a per-session `ConfirmGate` in `hub/` hold the call and make the yes decision. The session arms the gate and owns the timer. Feasible with changes.
- Path 2: event bus. A `ConfirmController` reacts to session events. Feasible, not recommended: it still needs a synchronous pipeline hook, and it adds about a third more code and race mitigations.
- Path 3: pipeline-centric. The pipeline decides the yes and calls `run_held()`. Feasible with changes, rejected: weak on T2 and T3, because the hub trusts the pipeline.
- Path 4: session state machine. Feasible, rejected for #30: it rewrites the abort and `reply.end` rules, outside the scope. Kept as a follow-up.

## Decision
The owner delegated G1 to the reviewers (owner's words: "Architect, dba and other agents do a decision"). The solution-architect reviewer picked Path 1, with a private release path and a pure `confirm_text.py` from Path 4 (2026-11-21). No data or roles are in scope, so no dba review. Reasons: the hold, the yes match and the single-use run all live in `hub/` (T2, T3, F4). The confirmation turn never calls `read_chat` (T7, T8, T9). It is the least code, and the session change is additive (T5, M5).

- D1: `hub/confirm_text.py` (pure). Fixed EN/PL/UK texts: the question with the source and the bare tool name (for example "device set_volume"), so it names what runs (T6), "Done.", "That did not work.", "Cancelled.". `is_yes(text, language)`: lowercase, strip punctuation and spaces at the ends, exact match of the whole text against the yes words of that language only (T7).
- D2: `hub/confirm.py`. `Held` (frozen: full name, a copy of the validated args, call id, turn). `ConfirmGate` holds at most one `Held` with a phase: held, armed, claimed. Methods: `hold`, `arm(turn) -> Held | None`, `expire(token) -> bool` (acts only if the same `Held` is still armed), `claim(turn)`, `pop()`, `drop()`, `drop_if_claimed(turn)`, `drop_if_held(turn)`. All checks bind to the `Held` object, because turn ids wrap at 256.
- D3: Chain step `confirm(specs, gate)` after `validate`. A spec with `confirm=false` passes (E8). Otherwise `gate.hold` and the outcome `ToolOutcome(False, "held: the user must say yes", "confirm_required")` (F3), or `confirm_busy` when a call is already held (E1). An exception in the step logs an error and returns `confirm_required`, and nothing runs (F4). Chain: wrap, resolve, limit, validate, confirm, terminal.
- D4: `CallCtx.turn: int`. `McpHub.turn_context(turn)` and `DeviceSession.turn_tools(turn)` take the turn. `produce()` gets it from `reply(turn, ...)`.
- D5: `McpHub(sources, language)`. `DeviceSession` gets the language at construction, from the same setting as `VoicePipeline(language)`. The pipeline never supplies the language for the decision.
- D6: `TurnTools.confirmation(text) -> str | None`, in this order: None unless the gate is claimed by `ctx.turn`. `held = gate.pop()` (gone before the run, T3). If `is_yes`, run the private `McpHub._release(held)` (wrap and terminal only: no limit, E9, never raises, F1, F2) and return "Done." or the failure text by `outcome.ok`. Else return "Cancelled.". Any exception logs, drops and returns "Cancelled." (F4). `_release` is not on `ToolContext`.
- D7: `TurnTools.held_question() -> str | None`: the fixed question when the gate holds a call whose turn is `ctx.turn` (the same binding as D6).
- D8: Pipeline. In `_turn`, right after `notify(text)`, call `tools.confirmation(text)`. If it returns text, put it on the `SentenceFeed`, end it, and do not start `read_chat`. In `agent.tool_rounds`, after `run_tools`, if `tools.held_question()` is not None, put the question on the feed and return: no further LLM round. LLM text streamed before the tool call has already played (accepted). Nothing but the fixed question follows the hold. The question goes through `speak()`, so it is moderated like any text.
- D9: Session.
  - `play()` returns its reason. After `finish(reply.end)` in `reply()`, if the reason is `done`, `gate.arm(turn)`. If it returns a token, send `state {confirm}`, then `confirm.request {turn, tool, timeout_ms: 10000}`, then start the timer task (injectable sleep). Any other end (error, cap, moderation, a `TurnError` before speech) leaves the call in the phase held, and the `reply()` `finally` (next item) runs `gate.drop_if_held(turn)`: it drops only a `Held` in the phase held whose turn is this turn, never an armed or claimed call. The early returns of `play()` (`finish(error)` before speech) and the `no_speech` pre-filter in `reply()` give a reason that is not `done`, so `arm` never runs on them.
  - Timer: after the sleep, if `gate.expire(token)`, send `error {turn: token.turn, code: confirm_timeout}` (T10).
  - `listen.start`: the gate code runs after the `too_many_turns` return (E7). After the new turn id: if the gate is armed, cancel the timer and `gate.claim(self.turn)` (E2). If the gate is claimed by an older turn (its `listen.stop` never came), `gate.drop()`.
  - In the `reply()` `finally`, also when the task is cancelled: `gate.drop_if_held(turn)` (a no-op after `arm`) and `gate.drop_if_claimed(turn)` (E6, F5; a no-op after `pop`).
  - `abort` (matching turn) and `close()`: `gate.drop()` and cancel the timer (E3, E4). `cancel_reply` does not drop the gate, or E2 breaks.
- D10: Flags. `McpServerConfig.no_confirm: list[str]` (default empty). A name that is not a key of `tools` rejects the entry. `McpSource` sets `confirm=True` unless the tool is in `no_confirm`. `DeviceSource`: `set_volume` `confirm=True`, `set_face` and `get_status` `confirm=False`.
- D11: Docs. `docs/protocol.md` confirm section gets the fixed texts, the exact-match rule and the turn rules. `docs/security.md` and the servers file example get `no_confirm`.
- D12: Tests (fake sources, fake clock, no board, no external service): the M1-M7 cases, plus: the LLM stream after `confirm_required` makes no second `chat_turn` and the question is the last chunk; a cap or moderation cut of the question gives no `confirm.request` and 0 calls; a `too_many_turns` refusal during the wait still times out at 10 s; `listen.start` during the wait and then `no_speech` gives 0 calls and no `confirm_timeout`; a claimed call with no `listen.stop`, then a new `listen.start`, is dropped (0 calls on a later yes); a `listen.start` during the question gives 0 calls, and a new `confirm` call in that turn is held, not `confirm_busy`.

## Consequences
- Accepted risk (from `docs/security.md`): anyone in earshot can say "yes".
- LLM text spoken before the tool call can still play before the fixed question. The LLM cannot claim success after the hold. OPEN (owner, later): accept this UX. The safety goal T6 is met.
- `set_volume` now needs a spoken yes. A volume change by voice takes two turns.
- The firmware does not yet handle `confirm.request` or `state confirm`. Follow-up: the device confirm face. The flow still works by voice.
- Follow-up: Path 4 session FSM, if the session gains more modes.
- The servers file gains `no_confirm`. #28 is not released, so no migration is needed.

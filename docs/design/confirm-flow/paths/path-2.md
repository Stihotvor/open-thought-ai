# Path 2 — event-driven controller

Core: the session publishes lifecycle events on a small in-process bus. The hub confirm step holds the call and publishes `ConfirmNeeded`. A per-session `ConfirmController` subscribes and owns the phase (idle, asked, waiting) and the timer.

## Modules
- `hub/events.py`: `EventBus` (subscribe, async publish, handlers in order). Events: ConfirmNeeded, ReplyEnd, ListenStart, Transcript, Abort, Closed, TurnRefused, NoSpeech, TurnFailed.
- `hub/confirm.py`: confirm step and `HeldSlot` (single-use `take`). `ConfirmController` with the phase, timer and injectable clock.
- `hub/confirm_text.py`: fixed texts and `is_yes`.
- `CallCtx.turn`, `McpHub.run_held` (skips limit).
- `mcp/config.py` `no_confirm`; `set_volume` confirm.
- `session.py` publishes the events.

## Confirmation turn
After `notify(text)` the pipeline calls a synchronous `tools.confirm_intercept(text)`, because a bus handler cannot veto the pipeline. The fixed text goes on the `SentenceFeed`; `read_chat` never starts. So the controller is event-driven for the timer and lifecycle, but call-driven for the bypass.

## Edge cases
E1, E8, E9 in the step. E2-E7 and F5 by events (E7: TurnRefused is a no-op). Timeout sends `confirm_timeout`.

## Tests
Bus and controller unit tests with a fake clock. Hub chain tests with a fake `ToolSource`. Session tests for M5. Pipeline test with 0 chat calls.

## Assessment
Effort M, about 4 new modules. The bus adds about a third more code than direct calls (estimate). Main risk: events arrive in an order the session does not guarantee (`cancel_reply` from several places, timer and reply task races). Mitigation: a lock, a phase counter. Feasible, no new dependency.

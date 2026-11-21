# Path 1 — hub-owned gate

Core: a `confirm` chain step and a per-session `ConfirmGate` inside the hub hold the call. The gate also makes the yes decision. The session calls small gate methods (after `reply.end`, on abort, on close, on a failed turn) and owns the timer.

## Modules
- `hub/confirm.py`: `ConfirmGate` holds at most one `Held(call, spec, turn)`. States idle, held, armed. `hold(call, turn)` returns `confirm_required` or `confirm_busy` (E1). `armed(turn)` after `reply.end`. `take(transcript, lang)` pops the call first (single use, T3), then matches the yes words (T7). `cancel(reason)` for abort, close, `no_speech`, moderation or STT failure, timeout. `expire()` for the timer.
- `hub/steps.py`: `confirm(specs, gate)` after `validate`. Non-confirm calls pass. Any exception returns `confirm_required` (F4).
- `hub/hub.py`: owns the gate. `run_held(held)` calls `_terminal` (skips `limit`, E9; never raises, F1, F2). `CallCtx.turn`.
- `ToolContext.confirmation(text) -> str | None`: `TurnTools` calls `gate.take`, then `run_held`, and returns the fixed reply text.
- Fixed EN/PL/UK texts and yes words.
- `session.py`: after `reply.end` it sends `state confirm`, `confirm.request`, and starts the timer (injectable clock).
- `mcp/config.py`: `no_confirm`. `DeviceSource`: `set_volume` confirm.

## Confirmation turn
In `pipeline._turn`, right after `notify(text)`, the pipeline calls `tools.confirmation(text)`. With an armed call it puts the fixed text on the `SentenceFeed` and ends it. `read_chat` never starts. Without a held call it returns None, and the turn is normal.

## Edge cases
E1 busy. E2 the next turn consumes the gate. E3 abort cancels. E4 close cancels. E5 gate idle after timeout. E6 the session maps `no_speech` to cancel. E7 refusal before any gate call. E8 pass. E9 no second count.

## Tests
`test_confirm.py` with a fake `ToolSource` and a fake clock (M1-M4, race of yes and expiry). Session tests for M5. Pipeline test: no `read_chat` with an armed gate. Config tests (M6).

## Assessment
Effort M, about 6 files. Main risk: the pipeline hook is the one place where the chat is skipped. If the hook goes, the call still cannot run (the step blocks it), so it fails closed. Other risks: timer races with `reply.end` and `abort`. Feasibility high.

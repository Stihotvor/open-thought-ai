# Path 4 — explicit session state machine

Core: `DeviceSession` keeps a `SessionState` enum (idle, listening, replying, awaiting_confirm) and one table of typed transitions, which replaces the flags `listening`, `open_turn` and `reply_task`. The hub confirm step only holds the call. The state `awaiting_confirm` owns the held call, the 10 s timer, the classification and the run.

## Modules
- `hub/confirm.py`: `Held` (full name, validated args, turn, call id), `ConfirmGate` (hold, take, drop; at most one), `confirm()` chain step.
- `hub/hub.py`: chain wrap, resolve, limit, validate, confirm, terminal. `McpHub.release(held)` runs wrap and terminal only (skips limit, E9). Never raises. `CallCtx.turn`.
- `hub/types.py`: codes `confirm_required`, `confirm_busy`.
- `confirm_text.py`: fixed EN/PL/UK texts and `is_yes(transcript, lang)`.
- `session_fsm.py`: `SessionState`, events (ListenStart, ListenStop, Abort, Close, ReplyEnded, ConfirmTimeout, TurnRefused), pure `transition(state, event) -> (state, [Action])`.
- `session.py`: messages become events, an executor runs the actions, `ConfirmTimer` with an injectable clock.
- `pipeline.py`: `_turn` split into `hear(pcm) -> str` (VAD, self-test, STT, moderation) and `say(text)` (TTS of fixed text).

## Confirmation turn
In awaiting_confirm, `listen.stop` calls `hear`, then `is_yes`. Yes: release, then "Done." or the failure text. Else: drop, "Cancelled.". The LLM is never reached.

## Edge cases
E1 busy outcome. E2 any listen.start in awaiting_confirm. E3 abort drops and cancels the timer. E4 close drops. E5 idle after timeout. E6 no_speech drops. E7 TurnRefused is a no-op. E8 only `spec.confirm` calls are held. E9 release skips limit.

## Tests
Table test of `transition()` for every state and event. Fake clock for the timer. Session tests with a fake WebSocket. Hub tests with a fake `ToolSource`.

## Assessment
Effort M (session refactor; hub part S). Main risk: moving `cancel_reply`, `finish` and `open_turn` behind the FSM can break abort and `reply.end` rules. The `ReplySource` contract grows (`hear`, `say`). Feasibility high. Choose it when the session will gain more modes.

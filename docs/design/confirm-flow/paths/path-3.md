# Path 3 — pipeline-centric

Core: the hub confirm step only parks the validated call and tells the LLM "held". The `VoicePipeline` makes the confirmation turn a keyword short-circuit after STT and transcript moderation, before chat, and replaces the LLM reply with the fixed question. The session only adds `state confirm`, `confirm.request` and the timer.

## Modules
- `hub/confirm.py`: `HeldCall`, `ConfirmGate` (hold, take with turn check, drop).
- `hub/steps.py`: `confirm(specs, gate)` after `validate`; busy and fail-closed rules.
- `hub/hub.py` and `ToolContext`: `held()`, `run_held()`, `cancel_held()`. `run_held` skips `limit`.
- `confirm_text.py`: fixed texts, `is_yes`.
- `pipeline.py`: in `_turn`, after `notify`, `if tools.held()`: decide with `is_yes`, run or cancel, speak fixed text, return. After a normal chat that held a call, speak the fixed question.
- `session.py`: timer after `reply.end`; hooks in `cancel_reply` and `close`.
- `mcp/config.py` `no_confirm`; `set_volume` confirm.

## Edge cases
E1 busy. E2 next turn takes the short path. E3 abort drops. E4 close drops. E5 timer dropped. E6 and F5 error path cancels. E7 the timer must survive a refused turn (cancel after the limit check). E8 pass. E9 no second count.

## Tests
Gate and step unit tests. Pipeline tests with a fake `ModelClient`: 0 chat calls, 1 source call. Session tests with a fake clock. Config tests.

## Assessment
Effort M, six files. Main risk: T2 and T3 are weaker. The yes decision is in the pipeline, and the hub trusts its `run_held()` call. A pipeline bug could run a held call without a yes. LLM sentences before the tool call may already be streamed before the fixed question. Feasibility high.

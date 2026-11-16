# Decision — VAD and the `transcript` message (issue #21)

## Context
Issue #21 still needs server VAD and the `transcript` message (`server/src/ota_server/pipeline.py` docstring). Requirements: `requirements.md`. Protocol v1 does not change. Budget 0 €. The echo source must keep its behaviour (T6).

## Options
- Path 1, in-band event: the pipeline yields a `Transcript` item in the reply stream. Feasible with a fix in `play`.
- Path 2, notify port: the pipeline calls an outbound `notify(text)` that the session gives it. Feasible.
- Path 3, split hear/answer: the session runs two source steps and sends `transcript` between them. Feasible with a fix. Most test churn.
- Path 4, streaming VAD at ingest: a stateful gate in `handle_audio`. Feasible with fixes. It changes echo (T6).

## Decision
Path 2, chosen by the user at G1 on 2026-11-16.
- `vad.py` (new, pure, stdlib only): `trim_speech(pcm: bytes) -> bytes | None`. 20 ms frames, RMS with `math.sumprod` over `array('h')`. Named constants `ENERGY_THRESHOLD` (start value 500, above the M2 noise level of 300), `MIN_SPEECH_MS = 300`, `MARGIN_MS = 200`. A last frame shorter than 20 ms is classified like a full frame. It returns None when the speech frames total less than 300 ms. It returns the span from the first to the last speech frame, plus the margin on each side, clipped to the input. It raises `ValueError` on an odd byte count. No `audioop` (removed in Python 3.13). No numpy.
- `reply.py`: `Notify = Callable[[str], Awaitable[None]]`. `ReplySource` becomes a Protocol: `__call__(pcm, history, *, notify: Notify = <no-op>)`. `echo` and every test fake source get the same keyword-only `notify` parameter with the no-op default, so mypy accepts them and `produce` can always pass `notify=`. Their behaviour does not change.
- `pipeline.py`: `__call__` runs `trim_speech` first, before `check_moderation`, so a silent turn makes no provider call at all. It runs through `asyncio.to_thread`, like `wav_to_pcm`, so audio pacing does not stop. None → `TurnError("no_speech")`, no STT call. `ValueError` → `TurnError("internal")`. STT gets the trimmed audio. After transcript moderation passes, `await notify(text)`. Chat gets the full text. The docstring line "Not yet here: the `transcript` message and VAD." is replaced by a short VAD and transcript note.
- `session.py`: `produce` passes `notify=` a per-turn closure. It sends `{"type": "transcript", "turn": turn, "text": text[:200]}` only while `open_turn == turn`. The `MIN_SPEECH` length check in `reply` stays as a cheap pre-filter, so echo is unchanged.

## Consequences
- The device sees `thinking` → `transcript` → `speaking` → `reply.start` → audio → `reply.end`.
- Silence and noise cost no STT call. VAD runs in the pipeline only, so echo does not use it.
- F4 holds because the pipeline awaits `notify` before it yields or raises. A later source that calls `notify` outside the turn task would lose that order. The turn guard in the closure is the second line.
- F3: a failed `transcript` send raises inside the pipeline. `play` handles it like any other non-TurnError failure: it logs "reply failed" and tries an `internal` error, whose send also fails on a closed socket and ends the reply task. This is accepted. No new error path is added.
- The threshold is a guess until it is measured on real device audio (F2). The tests use synthetic audio only.
- Given up: VAD work during upload (path 4), and the session control of the order that path 3 gives.

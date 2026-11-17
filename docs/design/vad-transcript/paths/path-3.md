# Path 3 — Split stages, orchestrated by the session

- `ReplySource` becomes a Protocol with two steps: `hear(pcm) -> Heard` and `answer(heard, history) -> AsyncGenerator[ReplyChunk]`. `Heard` holds the full `text` and an opaque `ctx` (the turn budget, so the deadline and call cap span both steps).
- `vad.py` (new, pure): `trim_speech(pcm) -> bytes | None`, energy-based, named constants.
- `DeviceSession.reply` orchestrates: VAD gate → `no_speech` with no `thinking` and no STT; `state thinking`; `hear` (STT, empty check, transcript moderation); send `transcript {turn, text[:200]}` when text is not empty; then the existing produce/play on `answer`.
- Echo becomes `EchoSource`: empty text, so no transcript is sent.
- F4 and E7 hold by construction: one sequential task, and `finish` is the last send.
- F1: `reply` gets a new try/except that ends the turn with `internal`.
- Effort: M. Interface, session and pipeline change. Every fake source in the tests changes, or a `__call__` shim stays.
- Risks: shared pipeline state across two calls through `Heard.ctx`. Broad test churn (M6).

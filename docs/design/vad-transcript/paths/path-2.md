# Path 2 — Hexagonal port (outbound notify callback)

- `reply.py`: new type `Notify = Callable[[str], Awaitable[None]]`. `ReplySource` gets a third parameter `notify`. `ReplyChunk` does not change.
- `vad.py` (new, pure): `trim_speech(pcm) -> bytes | None`, RMS threshold, `MIN_SPEECH_MS = 300`, `MARGIN_MS`. ValueError on an odd byte count.
- `session.py`: `produce` builds a per-turn `notify` closure: cut to 200 chars, check `open_turn == turn`, send `transcript`. Echo ignores `notify`.
- `pipeline.py`: VAD before STT (None → `no_speech`, no STT call). STT on trimmed audio, empty check, moderation, `await notify(text)`, then chat with the full text.
- F4: the pipeline awaits `notify` before it yields or raises, and the closure checks the turn as a second guard. E7: the abort cancels the producer.
- Effort: S–M. One module and one signature change. Every fake source in the tests changes.
- Risks: signature churn in tests. VAD speed in pure Python (M4) is unproven. The F4 order holds only while `notify` runs inside the turn task.

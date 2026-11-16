# Path 1 — In-band event stream

- `reply.py`: new frozen dataclass `Transcript(text)`. `ReplySource` yields `ReplyChunk | Transcript`. Echo is not changed.
- `vad.py` (new, pure): `trim_speech(pcm) -> bytes | None`. 20 ms frame RMS against named constants `ENERGY_THRESHOLD`, `MARGIN_MS`, `MIN_SPEECH_MS = 300`. Raises ValueError on an odd byte count. No new dependency.
- `pipeline.py`: VAD before STT. None → `TurnError("no_speech")`, no STT call. STT gets the trimmed audio. After transcript moderation passes, yield `Transcript(text[:200])`, then chat with the full text.
- `session.py`: `play` sends `{"type": "transcript", turn, text}` for a Transcript item. It does not touch `played`, `speaking` or the cap.
- Order: the existing queue and the serial `play` loop give thinking → transcript → speaking → reply.start → audio → reply.end. `finish` is the last send, so F4 holds.
- E7: an abort cancels the task. A Transcript still in the queue is never sent.
- Effort: S–M. About 3 files and one new module.
- Risks: energy VAD tuning on real audio (F2). A transcript can reach the device just before an abort. The wider yield type touches fakes and mypy unions.

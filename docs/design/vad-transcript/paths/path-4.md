# Path 4 — Streaming VAD at ingest

- `vad.py` (new): stateful `SpeechGate` with `feed(pcm)`, `finish() -> (bytes, speech_ms)`, `reset()`. Carry buffer for partial 20 ms frames, pre-roll ring, trailing margin, speech counter. Pure-Python RMS by default. `webrtcvad-wheels` is an optional upgrade behind the same interface (wheels not verified against T2).
- `DeviceSession`: `self.gate` replaces `self.audio`. `handle_audio` feeds each frame. `listen.start` and abort reset the gate. `reply()` decides `no_speech` from `speech_ms` before any source call, so echo also gets trimmed audio.
- Transcript: `VoicePipeline.__call__` gets an optional `on_transcript` async callback. The session passes `send_transcript` (cut to 200 chars).
- T5: the work is done during upload, so at `listen.stop` only one flush is left.
- F1: `handle_audio` runs in the receive loop, so it catches the error, marks the gate broken, and `reply()` sends `internal`.
- Effort: M. Session, pipeline and a new stateful module. Needs a threshold calibration pass.
- Risks: per-frame Python loop on the event loop in the receive path. Stale state if a reset is missed. Fixed threshold (F2).

# Path 3 — Timed client proxy

`TimedClient` wraps `ModelClient` and records each call into a per-turn `TurnTimer` held in a `ContextVar`. The pipeline adds marks for VAD and the first chunk and logs one line in a `finally`.
- Stages: `stt`, `mod_transcript`, `chat_first_token`, `chat_total`, `mod_reply`, `tts_1`, plus `vad` and `latency_ms` from pipeline marks.
- The proxy cannot see sentence boundaries, VAD or resample time. `mod_reply` covers the whole reply.
- Effort M. Risk: a ContextVar can be lost across `to_thread` or task boundaries, and two moderation calls are told apart by call order only.

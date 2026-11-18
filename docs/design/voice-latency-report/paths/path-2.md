# Path 2 — Observer port

`StageObserver` Protocol (`stage(turn, name, "start"|"end", t)`) with a no-op default, injected into `VoicePipeline`. Adapters: `LogObserver` (one info line per turn), `CollectingObserver` (harness).
- Events: `vad`, `stt`, `moderation_transcript`, `chat_first_sentence` (end at the first `SENTENCE_END`, a small scan in the delta loop), `moderation_reply` (whole reply), `tts_first_chunk`, `turn`.
- Clock read in the pipeline (`perf_counter`). `safe(observer)` catches errors (F1).
- Harness writes JSONL per turn (stages, code, warm-up, WER). Report reads the JSONL, not logs.
- Effort M. Risk: the first-sentence scan changes the `"".join` loop. Moderation still waits for the whole reply.

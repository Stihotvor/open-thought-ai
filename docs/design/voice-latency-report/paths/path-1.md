# Path 1 — Inline stage timer

A per-turn `StageTimer` (`timing.py`) is created in `VoicePipeline.__call__`. The pipeline marks each stage end and logs one line in a `finally`:
`turn.latency turn=<id> lang=<l> ok=<0|1> code=<c> latency_ms=.. vad=.. stt=.. mod_transcript=.. chat=.. mod_reply=.. tts1=.. selftest=..`
- `format_line` and `parse_line` live together in `timing.py` and share one test.
- Harness CLI (`--dir --pause --resume --out`) captures the line through a log handler and writes a side JSONL with phrase id and STT text (WER).
- Report parses the log with `parse_line`: p50/p95 per stage and language against the budget, warm-up excluded, failures by code.
- F1: every mark and the emit catch `Exception`.
- Effort M. Risk: marks spread through the long `__call__`. `chat` is to the full reply and `mod_reply` covers the whole reply, so the names must not claim "first sentence".

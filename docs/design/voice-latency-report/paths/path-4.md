# Path 4 — Structured event log, offline batch

`timing.emit(turn, stage, ms)` writes one JSON record per stage to logger `ota.timing` (optional `OTA_TIMING_LOG` file). No in-process aggregation.
- Records: `{"ev":"stage","turn","stage","ms","lang","seq"}` and a closing `{"ev":"turn","ms":latency_ms,"code"}`. Stages: `vad`, `stt`, `mod_transcript`, `chat_first` (small first-sentence mark), `mod_s1` (whole reply), `tts_s1`.
- Harness only drives turns. A stdlib report script parses JSONL from a harness run or a real device session. A shared `STAGES` constant is used by both sides.
- Effort M. Risk: stage names drift between writer and reader, and the report shows empty cells silently.

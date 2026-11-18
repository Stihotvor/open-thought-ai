# Decision — voice test set and latency report (issue #26)

## Context
Issue #26 asks for a test set of 30 phrases × 3 languages and a latency report toward p50 ≤ 5 s (`requirements.md`). The server times no stage today. The pipeline waits for the whole chat reply, moderates the whole reply, then speaks. So the budget rows "LLM to first sentence" and "moderation of sentence 1" do not exist as separate steps.

## Options
- Path 1, inline timer: a per-turn timer in `VoicePipeline`, one log line per turn. Feasible.
- Path 2, observer port: a `StageObserver` Protocol with two adapters. Feasible, more code for one consumer.
- Path 3, timed client proxy: cannot see VAD, resample or sentence ends. Rejected by the architect.
- Path 4, structured event log: one JSON record per stage, offline batch. Feasible, risk of name drift between writer and reader.

## Decision
Path 1 with parts of Paths 2 and 4, chosen by the user at G1 on 2026-11-18.
- `ota_server/timing.py`: `StageTimer` (`time.perf_counter`), `STAGES` constant, `format_line` and `parse_line` in one module. Every timer call catches `Exception` and logs it (F1).
- `pipeline.py`: marks `vad`, `stt`, `mod_transcript`, `chat_first_sentence` (passive: the delta join becomes a loop that notes the first `SENTENCE_END`, behaviour unchanged), `chat_full`, `mod_reply`, `tts1` (first `speak` plus `wav_to_pcm`), and `selftest` when the self-test runs. `latency_ms` is stamped at the first yield. One info line `turn.latency ...` is logged in a `finally`, also for failed turns with their code and no latency. No text in the line (F5).
- `ota_server/latency_run.py` (`python -m`): builds `VoicePipeline` from `OTA_<ROLE>_*`, plays the WAV files of one language in order with a pause, captures the log line, and writes a side JSONL per turn (phrase id, language, code, STT text for WER). `--resume` skips done ids.
- `ota_server/latency_report.py` (`python -m`): reads the captured lines and the side JSONL. p50 and p95 per stage and language, the budget row next to each, warm-up (first turn) left out, failures by code, missing stages counted, WER per language, `chat_full − chat_first_sentence` as the time that streaming could save. Rows the server cannot see (upload tail, network and device buffer) show "not measured". A footnote says the budget assumes per-sentence streaming.
- `docs/research/m3-phrases.json` (or similar): 30 ids × EN/PL/UK, family-safe, written by Claude.
- `server/scripts/make_phrase_audio.py`: Piper voices through speaches, 16 kHz mono WAV, stops before any model call if speaches is down. Not committed audio.
- `docs/guides/latency-run.md`: how to make audio, run, and report, plus the LiteLLM checklist from issue #20.

## Consequences
- Real device turns also log stage times.
- The report shows the gap to the streaming budget as a measured number.
- Follow-up (out of scope): per-sentence streaming of moderation and TTS, as `docs/architecture.md` describes. The architecture text still shows that design.
- The owner runs the real measurement (M6). Tests use fakes only.
- Given up: the observer port (Path 2) and the per-stage JSON log (Path 4).

## Run record (shared by T5 and T6)
One JSON object per line in `<out>/run.jsonl`:
`{"run": "<run id>", "lang": "en|pl|uk", "phrase_id": "p01", "turn_id": 1, "code": "ok|<TurnError code>", "latency_ms": 4200 | null, "stages": {"vad": 5, ...}, "stt_text": "..." | null}`.
The harness fills `turn_id`, `code`, `latency_ms` and `stages` from `parse_line` of the timing log line. The report takes the reference text for WER from `docs/research/m3-phrases.json`.

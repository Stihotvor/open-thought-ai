# Requirements — voice test set and latency report (issue #26)

User choices (2026-11-17): stage timing in the server, phrase text in the repo with audio made locally, phrases written by Claude, a manual LiteLLM checklist in scope.

## Business Goals
- B1: Prove the M3 exit goal: an EN/PL/UK round trip with a median (p50) latency of 5 s or less from button release to first sound.
- B2: Find the slow stage. Every turn reports the time of each stage, so a slow turn points to STT, chat, moderation or TTS.
- B3: Give the owner one command that runs the 90 phrases against real endpoints and writes a report.
- B4: Close the open checks from issue #20: the LiteLLM model ids and the STT/TTS fallback.

## Technical Constraints
- T1: Python 3.12 server, strict mypy, ruff. Tests use fake providers only. No real API call in tests.
- T2: The server logs stage times for every real turn: `turn.latency_ms` plus one time per stage. Names describe what the code does today (`vad`, `stt`, `mod_transcript`, `chat_first_sentence`, `chat_full`, `mod_reply`, `tts1`). The report maps each one to a budget row in `docs/architecture.md` with a footnote where they differ.
- T3: Timing adds no provider call and no new dependency. It uses a monotonic clock.
- T4: The repo holds the phrase text only: 30 phrases for each of EN, PL and UK, the same 30 meanings, family-safe. No audio file is committed.
- T5: A script makes 16 kHz mono s16le WAV input from the text with Piper voices through a local speaches server.
- T6: The harness runs `VoicePipeline` without a device, with the roles from the environment (`OTA_<ROLE>_*`). The owner runs it with their own keys. Claude never runs it against real endpoints.
- T7: The report is Markdown: p50 and p95 per stage and per language, compared with the budget in `docs/architecture.md`.
- T8: The real-run guide has a manual checklist for the LiteLLM path from issue #20.
- T9: Budget 0 €, apart from the owner's own free-tier calls.

## Edge Cases
- E1: A turn fails (`no_speech`, `llm_failed`, `rate_limited`). The report counts it as a failure with its code and leaves it out of the latency numbers.
- E2: The free-tier rate limit stops the run. The harness waits between turns (a set pause) and can resume from the last phrase.
- E3: A reply has no first sentence (empty or refused). The chat and TTS stage times are missing for that turn, and the report says so.
- E4: speaches is not running. The audio script stops with a clear message, before any model call.
- E5: The STT text differs from the phrase. The report shows the word error rate per language as a side number. It does not fail the run.

## Failure Modes
- F1: Timing code must never break a turn. An error in timing is logged and the turn goes on.
- F2: A wrong clock (wall clock) gives wrong numbers after a time jump. Use `time.monotonic` / `perf_counter` only.
- F3: The first turn includes the moderation self-test and connection setup. The report marks the first turn per run as a warm-up and leaves it out of p50.
- F4: Synthetic Piper audio is cleaner than a real microphone. The report states that the input is synthetic.
- F5: Logs must not hold the transcript or the reply text at info level. Only times, codes and the turn id.

## Success Metrics
- M1: Phrase file: exactly 30 phrases × 3 languages (90), with the same ids in each language. A test checks this.
- M2: Every turn with a fake provider logs `turn.latency_ms` and all stage times. A test checks the fields.
- M3: The report script turns a fixed sample log into a report with p50 and p95 per stage and language. A test checks the numbers.
- M4: Timing overhead is ≤ 1 ms per turn in a fake-provider test.
- M5: Existing checks stay green: pytest, ruff, mypy with 0 errors.
- M6: The real run (by the owner) gives p50 ≤ 5 s per language. OPEN: measured by the owner after the merge, not by the tests.

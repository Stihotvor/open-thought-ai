# QA results — voice-latency-report

Run: `server/.venv/bin/pytest -q`: 328 passed, 0 failed (2026-11-18). ruff check and format: clean. mypy (strict, `src tests scripts`): 0 errors.
The main session mapped the cases, because a path hook blocked the test-engineer on `server/tests/` in the previous issue.
Files: TI=test_timing.py, PH=test_phrases.py, PL=test_pipeline_latency.py, LR=test_latency_report.py, RU=test_latency_run.py, AU=test_make_phrase_audio.py, GD=test_latency_guide.py.

Counts: pass 24, partial 7, fail 0, not-automated 1.

| Case | Status | Evidence / gap |
|---|---|---|
| QA-001 | pass | PH test_exactly_30_entries_with_unique_ids, test_each_entry_has_three_non_empty_languages |
| QA-002 | pass | PH test_same_ids_in_every_language |
| QA-003 | partial | PH checks that every entry has all 3 languages. No test builds a broken file to check that the message names the id and language. |
| QA-004 | partial | PH checks that no text is empty. Family-safe content is a manual review in the PR. |
| QA-005 | pass | GD test_no_audio_in_the_phrase_folder (no `.wav` under `docs/`, `latency-audio/` in `.gitignore`) |
| QA-006 | pass | PL test_a_good_turn_logs_all_stages |
| QA-007 | pass | PL test_selftest_shows_on_the_first_turn_only |
| QA-008 | pass | PL test_overhead_is_one_ms_or_less |
| QA-009 | pass | PL test_a_timer_error_does_not_break_a_turn, TI test_timer_error_does_not_raise |
| QA-010 | partial | The timer uses `time.perf_counter` (monotonic). No test simulates a wall-clock jump. |
| QA-011 | pass | PL test_no_text_in_the_log |
| QA-012 | pass | PL test_a_turn_refused_before_any_provider_call_logs_its_code, TI test_failed_turn_has_no_latency |
| QA-013 | partial | PL test_a_failed_turn_logs_its_code covers `llm_failed`. RU test_rate_limited_turn_is_not_done covers `rate_limited` in the harness. No pipeline test for the `rate_limited` log line. |
| QA-014 | pass | PL test_a_reply_without_a_sentence_end, LR test_missing_stage_counted |
| QA-015 | pass | LR test_exact_percentiles_per_stage_and_language, test_report_shows_the_fixed_values (interpolated percentiles, as specs/T6.md says) |
| QA-016 | pass | LR test_warm_up_left_out |
| QA-017 | pass | LR test_failures_counted_and_excluded, test_sample_failures |
| QA-018 | pass | LR test_missing_stage_counted |
| QA-019 | pass | LR test_budget_footnote_notes_and_not_measured |
| QA-020 | pass | LR test_budget_footnote_notes_and_not_measured |
| QA-021 | pass | LR test_streaming_gap, test_sample_streaming_gap |
| QA-022 | pass | LR test_budget_footnote_notes_and_not_measured |
| QA-023 | pass | LR test_wer_half, test_wer_normalises_case_and_punctuation, test_sample_wer |
| QA-024 | pass | RU test_order_and_pause |
| QA-025 | pass | RU test_resume, test_resume_ignores_other_language |
| QA-026 | pass | RU test_damaged_last_line_is_skipped, test_missing_newline_is_added |
| QA-027 | pass | AU test_speaches_down_stops_before_synthesis |
| QA-028 | pass | AU test_wav_is_16k_mono_s16 |
| QA-029 | partial | AU test_one_file_per_phrase_and_language runs once. A second run over existing files is not tested. |
| QA-030 | partial | GD test_guide_names_the_tools_and_target. The real run (M6, p50 ≤ 5 s) is the owner's, after the merge. |
| QA-031 | pass | pytest 328 passed, ruff clean, mypy 0 errors |
| QA-032 | not-automated | Every test uses fakes or `httpx.MockTransport`. Nothing blocks sockets to prove 0 outgoing connections. |

## Review (2026-11-18)
Verdict: PASS WITH WARNINGS. Gate 6/6 pass, TDD order pass. 0 critical or high findings. 5 medium and 10 low.
Fixed: `voice_roles` moved to `endpoints.py` (the harness no longer imports `main`), damaged last line cut in place with one helper and one warning, phrase ids checked in the audio script, voice dict, named p50/p95 cut indices, upload-tail label constant, relative import, `TurnClock.closed()`, first-sentence scan on the new tail only, two long guide sentences split.
Stays, with reason:
- A resumed run gets a new run id, so the report drops its first turn as warm-up. A resume is a new process with cold connections, so that turn is a real warm-up turn.
- `TurnClock` stays in `pipeline.py`: it uses the pipeline's `SENTENCE_END` and is the pipeline's view of the timer.
- The harness reads the `turn.latency` line from the pipeline logger. This is a small coupling, and a test covers it (`test_lines_and_jsonl`, `test_pipeline_logger_level_is_restored`).
- A `rate_limited` turn is played again on `--resume`. The report counts each record, so the failure stays visible.
- `--base-url` of the audio script is not restricted: the operator sets it, and the phrase text is not sensitive.

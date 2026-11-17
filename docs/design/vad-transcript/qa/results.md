# QA results — vad-transcript

Run: `server/.venv/bin/pytest -q`, 236 passed, 0 failed (2026-11-16). ruff check and format: clean. mypy (project config): 4 errors, all present on main before this branch.
There is no Docker test suite: `compose.yaml` has only the `server` service. All cases run in pytest with the fake endpoint.

Files: V=test_vad.py, P=test_pipeline.py, PS=test_pipeline_session.py, S=test_session.py, G=test_gateway.py, N=test_qa_vad_transcript.py.

Counts: pass 33, partial 7, fail 0, not-automated 1.

| Case | Status | Evidence / gap |
|---|---|---|
| QA-001 | pass | V test_speech_between_silences_is_trimmed, P test_stt_gets_trimmed_audio |
| QA-002 | pass | N test_qa_002 |
| QA-003 | pass | P test_silence_makes_no_provider_call, N test_qa_no_speech |
| QA-004 | pass | P test_silence_makes_no_provider_call, N test_qa_no_speech |
| QA-005 | partial | 30 s noise gives no_speech. The 30 s limit without `listen.stop` runs with echo only (G test_30_s_of_audio_starts_the_reply). |
| QA-006 | pass | N test_qa_no_speech |
| QA-007 | pass | N test_qa_no_speech, V |
| QA-008 | pass | N test_qa_speech_reaches_stt, V |
| QA-009 | pass | N test_qa_speech_reaches_stt |
| QA-010 | pass | N test_qa_no_speech |
| QA-011 | pass | N test_qa_speech_reaches_stt |
| QA-012 | pass | N test_qa_no_speech[012], test_qa_012_repeat_works |
| QA-013 | pass | N test_qa_no_speech |
| QA-014 | pass | N test_qa_014_015, P test_odd_byte_count_is_internal |
| QA-015 | pass | N test_qa_014_015 |
| QA-016 | pass | PS test_device_message_order |
| QA-017 | pass | N test_qa_017 |
| QA-018 | pass | PS test_long_transcript_goes_whole_to_chat_and_cut_to_the_device |
| QA-019 | pass | S test_transcript_text_is_truncated |
| QA-020 | pass | N test_qa_020_021 |
| QA-021 | pass | N test_qa_020_021 |
| QA-022 | pass | N test_qa_022 (wire size computed in the test, not read from a real socket) |
| QA-023 | pass | N test_qa_023_024 |
| QA-024 | pass | N test_qa_023_024, P test_empty_transcript_is_no_speech_without_notify |
| QA-025 | pass | PS test_flagged_transcript_sends_no_transcript, N test_qa_025 |
| QA-026 | partial | The abort is injected by a delayed notify, not a real 2 s STT wait. S test_no_transcript_after_abort covers the guard. |
| QA-027 | pass | N test_qa_026_027_039 |
| QA-028 | partial | A failing transcript send causes no crash, and the next turn works. A real socket close is not exercised. |
| QA-029 | pass | N test_qa_029 |
| QA-030 | pass | N test_qa_030 |
| QA-031 | pass | V test_speed (about 17 ms for 30 s) |
| QA-032 | partial | N test_qa_032: the event loop never stalls 100 ms. Pacing of a second reply stream is not measured. |
| QA-033 | pass | S test_echo_sends_no_transcript, N test_qa_033 |
| QA-034 | pass | G test_short_audio_gives_no_speech |
| QA-035 | partial | The suite runs offline with the fake endpoint. Nothing blocks sockets to prove 0 outbound calls. |
| QA-036 | partial | Tests and ruff pass. mypy has 4 old errors in test files (baseline on main). |
| QA-037 | partial | Exact messages are asserted in PS and G. No schema check of every message. |
| QA-038 | pass | N test_qa_038 (waits 0.3 s) |
| QA-039 | pass | N test_qa_026_027_039 |
| QA-040 | not-automated | Needs a clean Python 3.12 environment and a PyPI install. |
| QA-041 | pass | N test_qa_speech_reaches_stt[041] |

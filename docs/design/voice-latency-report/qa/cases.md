# QA cases: voice test set and latency report (issue #26)

### QA-001 Phrase file has 90 phrases
- Covers: M1, T4, T3
- Type: functional
- Given the phrase file in the repo
- When a reader counts the phrases per language
- Then there are exactly 30 phrases in each of EN, PL and UK, 90 in total

### QA-002 Phrase ids match across languages
- Covers: M1
- Type: functional
- Given the phrase file with ids from the first to the thirtieth
- When a reader compares the id sets of EN, PL and UK
- Then the three sets are identical and no id is missing or extra

### QA-003 Phrase missing in one language
- Covers: M1
- Type: failure
- Given a copy of the phrase file where id 17 has no UK text
- When the phrase check runs on this copy
- Then the check fails and names id 17 and language UK

### QA-004 Phrase text is not empty and is family-safe
- Covers: T4
- Type: edge
- Given the 90 phrases
- When a reader reviews each text
- Then no text is empty or whitespace only, and none holds adult or violent content

### QA-005 No audio file committed
- Covers: T4
- Type: functional
- Given the repo after the change
- When a reader lists tracked files with extension .wav
- Then the list is empty

### QA-006 Fake turn logs total and every stage time
- Covers: M2, T2
- Type: functional
- Given a fake-provider turn with a reply of 2 sentences
- When the turn ends
- Then one log line holds turn.latency_ms and a time for vad, stt, mod_transcript, chat_first_sentence, chat_full, mod_reply and tts1

### QA-007 Self-test time logged on first turn
- Covers: M2, F3
- Type: functional
- Given a fresh server run with fake providers and the moderation self-test enabled
- When the first turn ends
- Then its log line holds a selftest time, and the second turn line holds none

### QA-008 Timing overhead is small
- Covers: M4
- Type: performance
- Given a fake-provider turn run 200 times with timing on and 200 times with timing off
- When the mean time per turn is compared
- Then the difference is 1 ms or less per turn

### QA-009 Timing error does not break the turn
- Covers: F1
- Type: failure
- Given a fake-provider turn where the timing code raises an error on the stt mark
- When the turn runs
- Then the turn finishes with the reply audio, and an error about timing is logged

### QA-010 Time jump does not change numbers
- Covers: F2
- Type: edge
- Given a fake turn where the wall clock moves back by 3600 s during the turn
- When the turn ends
- Then turn.latency_ms is zero or more and below 60000

### QA-011 Log line holds no text
- Covers: F5
- Type: security
- Given a fake turn with transcript "My secret word is pineapple" and a reply containing "pineapple soup"
- When the info log of the turn is read
- Then the word "pineapple" does not appear, and only times, a code and the turn id appear

### QA-012 Failed turn no_speech logs code without latency
- Covers: E1, T2
- Type: failure
- Given a fake STT that returns empty text
- When the turn ends
- Then the log line holds code no_speech and no turn.latency_ms

### QA-013 Failed turn llm_failed and rate_limited
- Covers: E1
- Type: failure
- Given a fake chat provider that fails once with an error and a second run with a 429 answer
- When both turns end
- Then the lines hold codes llm_failed and rate_limited, and neither holds turn.latency_ms

### QA-014 Reply with no first sentence
- Covers: E3
- Type: edge
- Given a fake chat reply that is an empty string
- When the turn ends and the report is built
- Then the line lacks chat_first_sentence and tts1 times, and the report counts one turn with missing stages

### QA-015 Report p50 and p95 per stage and language
- Covers: M3, T7
- Type: functional
- Given the fixed sample log and JSONL with 11 valid EN turns, stt values 100 to 1100 ms in steps of 100
- When the report is made
- Then the EN stt row shows p50 600 ms and p95 1050 ms, and PL and UK rows show their own values

### QA-016 Warm-up turn left out
- Covers: F3
- Type: edge
- Given a sample log where the first turn has stt 9000 ms and the other four turns have 1000 ms
- When the report is made
- Then the stt p50 is 1000 ms and the report marks the first turn as warm-up

### QA-017 Failures counted and excluded
- Covers: E1
- Type: edge
- Given a sample log with 8 good turns, 2 no_speech and 1 rate_limited
- When the report is made
- Then the report lists no_speech 2 and rate_limited 1, and the latency numbers use 8 turns or 7 after warm-up

### QA-018 Missing stages counted
- Covers: E3
- Type: edge
- Given a sample log where 3 turns have no tts1 time
- When the report is made
- Then the tts1 row shows 3 missing and the report says the stage is missing for those turns

### QA-019 Budget column and footnote
- Covers: T2, T7
- Type: functional
- Given the sample log
- When the report is made
- Then each stage row shows its budget value from the architecture table (stt 1.2 s, tts1 0.8 s), and a footnote states the budget assumes per-sentence streaming

### QA-020 Unmeasured rows
- Covers: T7
- Type: functional
- Given the sample log
- When the report is made
- Then the rows for upload tail and network plus device buffer show "not measured"

### QA-021 Streaming saving shown
- Covers: B2
- Type: functional
- Given a sample turn with chat_first_sentence 1500 ms and chat_full 3500 ms
- When the report is made
- Then the report shows 2000 ms as the time streaming could save

### QA-022 Synthetic input note
- Covers: F4
- Type: functional
- Given any sample log
- When the report is made
- Then the report states that the input audio is synthetic

### QA-023 Word error rate per language
- Covers: E5
- Type: functional
- Given JSONL where the EN phrase "turn on the light" got STT text "turn on the night" and others match
- When the report is made
- Then the report shows a WER per language, EN above 0, and the run does not fail

### QA-024 Pause between turns
- Covers: E2
- Type: functional
- Given the harness with fake providers, 3 WAV files and a pause of 2 s
- When the run plays them
- Then the start of each turn is at least 2 s after the end of the one before

### QA-025 Resume skips done ids
- Covers: E2
- Type: functional
- Given a side JSONL that holds ids 1 to 12 and 30 WAV files in EN
- When the harness runs again with --resume
- Then it plays ids 13 to 30 only and adds 18 rows

### QA-026 Resume with a corrupt last row
- Covers: E2
- Type: failure
- Given a side JSONL where the last line is cut in half
- When the harness runs with --resume
- Then it does not crash, and it repeats the phrase of the cut line
- OPEN: requirements do not say how to treat a damaged last row

### QA-027 Audio script with speaches down
- Covers: E4
- Type: failure
- Given no speaches server on the set address
- When the audio script runs
- Then it exits with a non-zero code and a clear message, and no model call is made

### QA-028 Audio format
- Covers: T5
- Type: functional
- Given a fake speaches that returns audio at 22050 Hz stereo
- When the audio script writes the WAV file for phrase 1 in PL
- Then the file is 16 kHz, mono, s16le

### QA-029 Audio script repeated
- Covers: T5
- Type: edge
- Given WAV files from an earlier run in the output folder
- When the script runs again with the same phrases
- Then it ends with 90 files and no error

### QA-030 Guide content and LiteLLM checklist
- Covers: T8, B4, M6
- Type: functional
- Given the guide file
- When a reader opens it
- Then it names the audio script, the run and the report, has a LiteLLM checklist for model ids and STT/TTS fallback, and states the target p50 of 5 s or less per language
- OPEN: M6 is measured by the owner after merge, so no automatic test covers it

### QA-031 Existing checks stay green
- Covers: M5
- Type: functional
- Given the branch with all tasks done
- When the test run, ruff and mypy run
- Then all tests pass and ruff and mypy report 0 errors

### QA-032 No real API call in tests
- Covers: T1, T6
- Type: security
- Given the test run with network access blocked
- When the full test suite runs
- Then all tests pass and no outgoing connection is made

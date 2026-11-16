# QA cases: VAD and transcript (issue #21)

### QA-001 Speech trimmed from long silence
- Covers: M1, E3
- Type: functional
- Given a voice turn whose audio is 1 s silence, 1 s tone-like speech at amplitude 8000, then 1 s silence, 16 kHz mono
- When the user releases the button (listen.stop)
- Then the audio the fake STT receives is at most 1.5 s long and contains the whole tone

### QA-002 Margin kept around speech
- Covers: E3
- Type: edge
- Given audio of 5 s silence, 1 s speech at amplitude 8000, 5 s silence
- When the user releases the button
- Then the STT audio is between 1.0 s and 1.5 s long, with a margin of silence on each side

### QA-003 3 s silence gives no_speech
- Covers: M2, B2, E1
- Type: functional
- Given 3 s of digital silence (all samples 0)
- When the user releases the button
- Then the device gets error no_speech and the fake STT is called 0 times

### QA-004 3 s low noise gives no_speech
- Covers: M2, E1
- Type: functional
- Given 3 s of noise with peak amplitude 300
- When the user releases the button
- Then the device gets error no_speech and the fake STT is called 0 times

### QA-005 Noise up to the 30 s limit
- Covers: E1
- Type: edge
- Given 30 s of noise with peak amplitude 300
- When the 30 s limit ends the turn
- Then the device gets error no_speech and the fake STT is called 0 times

### QA-006 Short click is rejected
- Covers: B2, E1
- Type: edge
- Given 3 s of silence with one 20 ms click at amplitude 20000
- When the user releases the button
- Then the device gets error no_speech and the fake STT is called 0 times

### QA-007 250 ms of speech is rejected
- Covers: M3, E2
- Type: edge
- Given 250 ms of speech at amplitude 8000 inside 2 s of silence
- When the user releases the button
- Then the device gets error no_speech and the fake STT is called 0 times

### QA-008 400 ms of speech is accepted
- Covers: M3
- Type: edge
- Given 400 ms of speech at amplitude 8000 inside 2 s of silence
- When the user releases the button
- Then the fake STT is called exactly 1 time

### QA-009 Speech split in two short parts
- Covers: E2
- Type: edge
- Given two bursts of speech of 200 ms each (400 ms total) separated by 1 s of silence
- When the user releases the button
- Then the fake STT is called exactly 1 time, because total speech is at least 300 ms
  Resolved: only speech frames count, summed across bursts (decision.md). Gaps do not count and do not reset the sum.

### QA-010 Two bursts under 300 ms in total
- Covers: E2
- Type: edge
- Given two bursts of speech of 100 ms each separated by 1 s of silence
- When the user releases the button
- Then the device gets error no_speech and the fake STT is called 0 times

### QA-011 Quiet speech above threshold
- Covers: F2
- Type: edge
- Given 1 s of speech at amplitude 1500 between 1 s silences
- When the user releases the button
- Then the fake STT is called exactly 1 time

### QA-012 Quiet speech below threshold is rejected, repeat works
- Covers: F2
- Type: failure
- Given 1 s of speech at amplitude 400 in a first turn, and 1 s at amplitude 8000 in a second turn
- When the user presses and releases the button for each turn in order
- Then turn 1 gets error no_speech, turn 2 reaches STT, and the session stays open

### QA-013 Empty audio
- Covers: E1
- Type: failure
- Given a turn with 0 bytes of audio
- When the user releases the button
- Then the device gets error no_speech and the fake STT is called 0 times

### QA-014 Odd byte count gives internal
- Covers: F1
- Type: failure
- Given audio of 32001 bytes (odd count)
- When the user releases the button
- Then the device gets error internal and the fake STT is called 0 times

### QA-015 Session survives internal error
- Covers: F1
- Type: failure
- Given a turn that just ended with error internal after 32001 bytes of audio
- When the user starts a new turn with 1 s of speech at amplitude 8000 and releases the button
- Then the second turn gets a transcript and a reply

### QA-016 Transcript before reply, full order
- Covers: M5, B1, T3
- Type: functional
- Given a fake STT returning "what is the weather" and a fake chat and TTS, with 1 s of speech at amplitude 8000
- When the user releases the button
- Then the device gets in order: state thinking, transcript {turn, text "what is the weather"}, state speaking, reply.start, audio, reply.end

### QA-017 Transcript carries the turn id
- Covers: B1, T3
- Type: functional
- Given turn id 7 and a fake STT returning "hello"
- When the user releases the button
- Then the transcript message has turn 7 and text "hello"

### QA-018 Long STT text truncated to 200
- Covers: E4, M5
- Type: edge
- Given a fake STT returning 300 characters
- When the user releases the button
- Then the transcript text is exactly the first 200 characters

### QA-019 Full text goes to chat
- Covers: E4
- Type: edge
- Given a fake STT returning 300 characters
- When the user releases the button
- Then the fake chat receives all 300 characters

### QA-020 Text of exactly 200 characters
- Covers: E4
- Type: edge
- Given a fake STT returning 200 characters
- When the user releases the button
- Then the transcript text has 200 characters, unchanged

### QA-021 Text of 201 characters
- Covers: E4
- Type: edge
- Given a fake STT returning 201 characters
- When the user releases the button
- Then the transcript text has 200 characters

### QA-022 Multibyte transcript
- Covers: M5, T3
- Type: edge
- Given a fake STT returning 250 Ukrainian characters ("привіт" repeated)
- When the user releases the button
- Then the transcript text is 200 characters and is valid text. Resolved: the limit is 200 characters (Python str). The server sends UTF-8 JSON without ASCII escapes, so 200 Cyrillic characters are about 400 bytes plus the envelope, below the 1024-byte frame limit.

### QA-023 Empty STT text
- Covers: E5
- Type: edge
- Given 1 s of speech and a fake STT returning ""
- When the user releases the button
- Then the device gets error no_speech and no transcript message

### QA-024 Whitespace-only STT text
- Covers: E5
- Type: edge
- Given 1 s of speech and a fake STT returning "   "
- When the user releases the button
- Then the device gets error no_speech and no transcript message
  Resolved: the pipeline strips the STT text, so whitespace-only text counts as empty.

### QA-025 Flagged transcript
- Covers: E6
- Type: security
- Given a fake STT returning "flagged phrase" and a fake moderation that flags it
- When the user releases the button
- Then the device gets error moderated and no transcript message, and chat is called 0 times

### QA-026 Abort during STT
- Covers: E7
- Type: edge
- Given a fake STT that waits 2 s before returning "hello"
- When the device sends abort for the turn 0.5 s after listen.stop
- Then the device gets reply.end reason abort and never gets a transcript for that turn

### QA-027 Abort then new turn
- Covers: E7
- Type: edge
- Given the aborted turn of QA-026
- When the user starts a new turn and the fake STT returns "second"
- Then the device gets transcript "second" with the new turn id only

### QA-028 Closed socket at transcript send
- Covers: F3
- Type: failure
- Given the socket closes while STT runs
- When the fake STT returns "hello"
- Then the server ends the turn with no crash, the server stays running, and the next connection can run a turn

### QA-029 Nothing after error or reply.end
- Covers: F4
- Type: failure
- Given a turn with a fake chat that fails with llm_failed
- When the user releases the button
- Then the device gets transcript before error llm_failed, and no transcript after the error

### QA-030 Repeated release for same turn
- Covers: F4
- Type: failure
- Given a finished turn 3 with reply.end sent
- When the device sends listen.stop for turn 3 again
- Then the device gets no transcript and no second reply for turn 3

### QA-031 VAD speed on 30 s
- Covers: M4, T5
- Type: performance
- Given 30 s of audio (960000 bytes) with 1 s of speech in the middle
- When VAD runs, best of 3 runs
- Then it takes at most 50 ms

### QA-032 Audio pacing not blocked by VAD
- Covers: T5
- Type: performance
- Given a 30 s turn running and a second reply stream being paced
- When VAD runs on the 30 s audio
- Then audio frames to the device keep their 100 ms pacing without a gap over 100 ms

### QA-033 Echo source unchanged
- Covers: T6
- Type: functional
- Given the default echo source and 1 s of audio at amplitude 8000
- When the user releases the button
- Then the device gets the echoed audio and no transcript message

### QA-034 Echo with silence
- Covers: T6
- Type: edge
- Given the echo source and 100 ms of audio (below the minimum length)
- When the user releases the button
- Then the device gets error no_speech as before

### QA-035 No real provider calls
- Covers: T6
- Type: security
- Given the test run with no network and no API keys
- When the full server test suite runs
- Then all tests pass and 0 outbound network calls occur

### QA-036 Existing suite and static checks
- Covers: M6
- Type: functional
- Given the finished change
- When the server test suite, ruff and mypy run
- Then all existing tests pass and ruff and mypy report 0 errors

### QA-037 Protocol unchanged
- Covers: T3, T4
- Type: functional
- Given a full voice turn
- When the user releases the button
- Then every message uses only the types and fields in protocol v1, with no new field

### QA-038 VAD does not end a turn alone
- Covers: T4
- Type: edge
- Given 1 s of speech then 5 s of silence, with no listen.stop sent
- When 5 s pass
- Then the server does not start STT or reply until listen.stop or the 30 s limit

### QA-039 Stale transcript dropped by turn id
- Covers: F4, T3
- Type: edge
- Given turn 4 aborted and turn 5 open, with the STT of turn 4 finishing late with "old"
- When the STT of turn 4 returns
- Then the device never gets a transcript with turn 4 text "old"

### QA-040 Free dependency check
- Covers: T1, T2
- Type: functional
- Given a clean Linux x86_64 Python 3.12 environment with no compiler and no GPU
- When the server installs from PyPI
- Then the install succeeds and no new paid or compiled dependency is added

### QA-041 Threshold constant above noise
- Covers: F2, M2
- Type: edge
- Given low noise at amplitude exactly 300 and speech at amplitude 700
- When each is sent as 1 s of audio
- Then the noise gets no_speech and the speech reaches STT

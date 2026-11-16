# Requirements — VAD and the `transcript` message (issue #21)

Remaining scope of issue #21. The STT → chat → TTS pipeline, moderation, per-sentence TTS, the 60 s cap and "continue" are merged (PRs 49–52).

## Business Goals
- B1: The user sees what the server heard. The device gets `transcript {turn, text}` before the reply, so a wrong answer can be traced to a wrong transcript.
- B2: A press with no real speech (a click, a cough, room noise) ends fast with `no_speech` and costs no STT call. Free-tier STT quota is small (C8 of the MVP requirements).
- B3: Close the last open item of issue #21, so milestone M3 can measure the EN/PL/UK round trip.

## Technical Constraints
- T1: Python 3.12 server. Input audio is PCM 16 kHz, mono, s16le (`server/src/ota_server/protocol.py`).
- T2: Budget 0 €. No paid service. A new dependency must be free, run on a 4-core CPU without GPU, and install from PyPI without a compiler on Linux x86_64.
- T3: Protocol v1 does not change. `transcript` is S→D with `turn` and `text`, truncated to 200 characters (`docs/protocol.md`). It goes after STT and before chat (`docs/architecture.md`, turn sequence).
- T4: The end of an utterance stays the button release (`listen.stop`) or the 30 s limit. The device is push-to-talk. VAD does not end a turn by itself (`listen.stop` in `docs/protocol.md` is the end signal).
- T5: VAD runs inside the latency budget line "Upload tail + VAD end": 0.2 s.
- T6: The echo source (M2, `OTA_PIPELINE` default) keeps working. Tests use fake providers only. No real API call.

## Edge Cases
- E1: Silence or noise only, for any length up to 30 s: `no_speech`, no STT call.
- E2: Speech shorter than 300 ms in total: `no_speech`, no STT call.
- E3: Speech with long silence before and after: STT gets the speech with a short margin, not the silence.
- E4: STT returns text longer than 200 characters: `transcript.text` holds the first 200 characters. The full text goes to chat.
- E5: STT returns empty text after VAD found speech: `no_speech`, as today. No `transcript` is sent.
- E6: Transcript moderation flags the text: the device gets `error {code: moderated}`. The flagged text is not sent as `transcript` (design choice: do not show flagged text back).
- E7: An abort arrives while STT runs: no `transcript` is sent for the cancelled turn.

## Failure Modes
- F1: The VAD code raises on bad audio (odd byte count): the turn ends with `internal`. The session survives.
- F2: VAD wrongly rejects quiet real speech: the user sees `no_speech` and repeats. The threshold is a named constant, so a field fix is one line.
- F3: The `transcript` send fails because the socket closed: the turn ends like any closed-socket case. No new error path.
- F4: The `transcript` message must never reach the device after `error` or `reply.end` of the same turn.

## Success Metrics
- M1: For a fixture with 1 s silence + 1 s tone-like speech + 1 s silence, the audio sent to STT is at most 1.5 s long.
- M2: For 3 s of silence and for 3 s of low noise (amplitude ≤ 300), STT is called 0 times and the device gets `no_speech`.
- M3: For 250 ms of speech, the device gets `no_speech`. For 400 ms of speech, STT is called once.
- M4: VAD on 30 s of audio takes ≤ 50 ms on the dev machine (well inside the 0.2 s budget).
- M5: In a full voice turn, the device message order is `state thinking` → `transcript` → `state speaking` → `reply.start` → audio → `reply.end`. `transcript.text` length ≤ 200.
- M6: Existing server tests stay green. ruff and mypy report 0 errors.

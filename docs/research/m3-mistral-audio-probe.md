# M3 probe: Mistral STT and TTS (2026-11-10)

Free tier key, direct Mistral API, one phrase per language.
Test: TTS makes a WAV, then STT reads it back.

## TTS: `voxtral-mini-tts-latest`, `POST /v1/audio/speech`
- A `voice` (or `ref_audio`) is required. All preset voices listed are `en_us`.
- Works on the free tier. About 1.1 s for a 3–5 s phrase (not streamed).
- Output: WAV 24 kHz, mono, 16 bit. The server must resample to 16 kHz.
- EN, PL and UK text all produce audio with the English voice `en_paul_neutral`.
  PL and UK quality is not judged yet: a human must listen.

## STT
| Model | Free tier | EN | PL | UK |
|---|---|---|---|---|
| `voxtral-mini-latest` (resolves to `voxtral-mini-transcribe-2602`) | yes, ~0.4 s | exact | garbled, Cyrillic output | partly right, mixed scripts |
| `voxtral-mini-transcribe-3-0` | probably no (429 on all 6 calls, also with 30 s pauses) | – | – | – |
| `voxtral-small-transcribe-3-0` | no (429 on every call) | – | – | – |
| `voxtral-small-latest` via `/v1/chat/completions` with `input_audio` | yes, strict pace limit | exact, 0.7 s | 429 | exact, but 121 s |

- The free tier has a strict pace limit. `voxtral-small-latest` gave a 429 three seconds after a good call,
  and one call took 121 s (queued). A server-side rate limiter is required (see the delivery plan, M3).
- `voxtral-mini-transcribe-2602` rejects `language=pl` and `language=uk`.
  Its list: ar, en, de, es, fr, hi, it, nl, pt, zh, ru, ko, ja.
- The PL and UK input came from an English voice, so part of the error can be the TTS accent.

## Result
- EN: Mistral STT and TTS meet the need on the free tier.
- PL and UK: the transcription endpoint on the free tier does not support them.
  The chat model `voxtral-small-latest` with audio input transcribed UK exactly (PL hit the limit),
  but free-tier pacing and the 121 s delay break the ≤ 5 s latency goal. It may work on a paid plan.
- Next: test a local STT for PL and UK (faster-whisper on the t630), and a human check of the PL/UK TTS audio.

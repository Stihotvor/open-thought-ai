# M3 probe: STT and TTS providers (2026-11-10)

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

## Other providers (same clips, chat with audio input)
| Model | EN | PL | UK |
|---|---|---|---|
| `gemini-3.5-flash` | exact, 2.2 s | wrong words, 7.9 s | exact, 2.0 s |
| `gemini-3.5-flash-lite` | exact, 2.5 s | wrong words, 1.7 s | exact (one call took 122 s) |
| `gemini-3.5-transcribe` | empty reply via the OpenAI-compatible route | – | – |
| `gemma-4-26b-a4b-it`, `gemma-4-31b-it` | 400: no audio input | – | – |

- Every model that reads UK exactly fails on PL. So the PL test clip itself is the likely problem
  (English voice reading Polish). A real human PL recording is needed before judging PL STT.
- Mistral `voxtral-mini-transcribe-3-0` and `voxtral-small-transcribe-3-0`: 429 on all 6 calls with
  30 s pauses, although the console shows a free-tier quota of 50k tokens/min and 1 req/s.
- Cloudflare Workers AI (docs only, not probed): Whisper large-v3-turbo for STT at about 47 neurons per
  audio minute, 10,000 free neurons per day. TTS has no confirmed PL or UK voice.

## Local models on homeserv (t630, container limit 1.2 CPU, 1.8 GB)
Input: Piper clips (EN lessac, PL gosia, UK ukrainian_tts), resampled to 16 kHz. Model loaded and warmed up first.

| Model | Load | Time per 3 s clip | EN | PL | UK |
|---|---|---|---|---|---|
| Piper TTS (warm), medium voices | – | 1.8–3.1 s for 2.5–3.1 s audio | ok | ok | voice drops Д and С |
| `parakeet-tdt-0.6b-v3` int8 (onnx-asr) | 33 s | 7.5–8.0 s | exact | right, comma only | 2 words wrong |
| `faster-whisper small` int8 | 13 s | 18 s | right, comma only | exact | 2 words wrong |
| `faster-whisper large-v3-turbo` int8 | 56 s | 93–96 s | exact | right, comma only | 2 words wrong |

- Accuracy is good for PL. The UK errors match the letters that the Piper UK voice drops
  ("Missing phoneme from id map: Д, С"), so the clip is at fault, not only the STT.
- Speed is the blocker. With 1.2 CPU, local STT is 2.5× to 32× slower than real time. The best one,
  Parakeet, needs about 8 s for a 3 s clip. That breaks the ≤ 5 s turn goal on its own.
- Piper runs at about real time on 1.2 CPU. Sentence streaming hides part of it (the first sentence plays
  while the next is made). Output is 22,050 Hz, so it needs a resample to 16 kHz.
- The UK voice `ukrainian_tts-medium` misses phonemes. Test `uk_UA-lada-x_low` (16 kHz) or other UK voices.

## Local models on the laptop (8 CPU threads, GTX 1050 4 GB, CUDA int8)
Input: fresh Piper clips. Two UK voices: `lada-x_low` and `ukrainian_tts-medium`. Model warmed up first.

| Model | Device | Load | Time per 3 s clip | EN | PL | UK (lada) | UK (ukrainian_tts) |
|---|---|---|---|---|---|---|---|
| Piper TTS | CPU | – | 0.11–0.18 s | ok | ok | ok (one diacritic missing) | drops Д and С |
| `parakeet-tdt-0.6b-v3` int8 | CPU | 2.5 s | 0.27–0.29 s | exact | exact | exact | wrong |
| `parakeet-tdt-0.6b-v3` int8 | "GPU" | 22.6 s | 0.23–0.28 s | exact | exact | exact | wrong |
| `faster-whisper small` | GPU | 17.7 s | 0.5 s (EN 1.7 s) | exact | exact | 1 word wrong | wrong |
| `faster-whisper medium` | GPU | 301 s (first download) | 1.2 s | exact | exact | 1 word wrong | wrong |
| `faster-whisper large-v3-turbo` | GPU | 40 s | 1.5 s | exact | exact | exact | wrong |

- "exact" ignores case and punctuation.
- Parakeet on the laptop CPU is the winner: exact EN, PL and UK in under 0.3 s, no GPU needed.
  The "GPU" run gives the same speed, so onnxruntime probably fell back to the CPU. Not checked.
- With Piper (about 0.15 s) the whole local speech path takes under 0.5 s per 3 s phrase on this laptop.
- `ukrainian_tts-medium` is not usable: every STT model fails on it. Use `lada-x_low` (16 kHz) for UK.
- On homeserv the same Parakeet model needed about 8 s. The 1.2 CPU limit explains part of it.
  Thread oversubscription inside the CPU quota may explain the rest. Open: test with 1–2 threads.

## Result
- EN: Mistral STT and TTS meet the need on the free tier.
- PL and UK: the transcription endpoint on the free tier does not support them.
  The chat model `voxtral-small-latest` with audio input transcribed UK exactly (PL hit the limit),
  but free-tier pacing and the 121 s delay break the ≤ 5 s latency goal. It may work on a paid plan.
- STT: local Parakeet TDT 0.6B v3 on a laptop CPU is the best option: exact EN, PL and UK in under 0.3 s,
  offline, free. Gemini Flash audio input (about 2 s) is the cloud fallback.
- TTS: local Piper for EN, PL and UK (`lada-x_low` for UK), about 0.15 s per phrase on the laptop CPU.
- Open: test with a real human voice, an OpenAI-compatible server for Parakeet and Piper, and the homeserv thread count.

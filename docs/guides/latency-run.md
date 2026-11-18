# Latency run and report

This guide measures the voice turn latency with real endpoints (issue #26).
It plays 30 phrases in each of EN, PL and UK through the voice pipeline.
Then it makes a Markdown report with p50 and p95 per stage and per language.
The last section is the manual LiteLLM checklist from issue #20.

The M6 target: p50 <= 5 s per language. The owner measures it after the merge.

Items marked **verify** are not confirmed.

## Prerequisites
- A local speaches server. See [speech-server.md](speech-server.md).
- The server virtual environment in `server/.venv`, with the server package installed.
- Your own keys in the `.env` file in the repo root. Do not commit `.env`. Do not share it.
- Time and quota for about 90 voice turns. Each turn makes about five provider calls (STT, two moderation calls, chat and TTS).
- A free-tier plan has a low rate limit. Use a pause between turns (`--pause`, default 5 s). Increase it if you get `rate_limited`.

Run all commands from the repo root. The run reads `.env` from the current directory.

## Steps

1. Start speaches and download the models.
   - Start speaches as in [speech-server.md](speech-server.md), steps 2 and 3.
   - Download one Piper voice for each language. The script defaults are below. The model id is `speaches-ai/piper-<voice>`.

     | Language | Piper voice | Status |
     |---|---|---|
     | en | `en_US-amy-medium` | **verify** |
     | pl | `pl_PL-gosia-medium` | **verify** |
     | uk | `uk_UA-ukrainian_tts-medium` | **verify** (speech-server.md names `uk_UA-lada-x_low`) |

   - List the voices that your speaches release serves: `uvx speaches-cli registry ls --task text-to-speech` (**verify** the task name).
   - Check that it runs: `curl http://localhost:8000/health`.

2. Make the input audio with `make_phrase_audio.py`.

   ```bash
   server/.venv/bin/python server/scripts/make_phrase_audio.py \
     --base-url http://localhost:8000/v1
   ```

   - The script reads `docs/research/m3-phrases.json`.
   - It writes `latency-audio/<lang>/<id>.wav` (16 kHz, mono, s16le). Git ignores `latency-audio/`.
   - Use `--voice-en`, `--voice-pl` and `--voice-uk` to set other Piper voices.
   - Use `--out` to write to a different folder.
   - The script sends no API key. If speaches has `API_KEY` set, the speech call can fail with 401 (**verify**). Then use a speaches instance without a key that listens on loopback only.
   - If speaches does not run, the script stops with an error before any speech call.

3. Set the model roles in `.env`.
   - Set `OTA_CHAT_*`, `OTA_STT_*`, `OTA_TTS_*` and `OTA_MODERATION_*` as for the real server. See `.env.example`.
   - Voice mode needs all four roles. Without one, the run stops with `config error`.
   - The run uses one TTS voice from `OTA_TTS_VOICE`. Set the voice for each language on the command line (step 4). Variables on the command line win over `.env`.
   - If your TTS model id is different for each voice (Piper on speaches), also set `OTA_TTS_MODEL` on the command line.

4. Run each language with `latency_run`. Use one output folder for all three languages.

   ```bash
   OTA_TTS_VOICE=<en voice> server/.venv/bin/python -m ota_server.latency_run \
     --audio latency-audio --lang en --out latency-audio/run-<date> --pause 5
   OTA_TTS_VOICE=<pl voice> server/.venv/bin/python -m ota_server.latency_run \
     --audio latency-audio --lang pl --out latency-audio/run-<date> --pause 5
   OTA_TTS_VOICE=<uk voice> server/.venv/bin/python -m ota_server.latency_run \
     --audio latency-audio --lang uk --out latency-audio/run-<date> --pause 5
   ```

   - The run plays the WAV files in id order, one voice turn for each file.
   - It prints one progress line for each turn: language, phrase id and code.
   - It writes `<out>/<lang>.log` (the `turn.latency` lines) and `<out>/run.jsonl` (one record for each turn).
   - `run.jsonl` holds the STT text. Do not commit it. Keep it in `latency-audio/`.

5. Make the report with `latency_report`.

   ```bash
   server/.venv/bin/python -m ota_server.latency_report latency-audio/run-<date>/run.jsonl \
     --phrases docs/research/m3-phrases.json > docs/research/m3-latency-<date>.md
   ```

   - Without `--phrases`, the report has no word error rate (WER).

6. Save the report.
   - Keep the report as `docs/research/m3-latency-<date>.md`. Use the date of the run, for example `m3-latency-2026-11-20.md`.
   - Add one line on top: the endpoints and models for each role, the speaches host (CPU or GPU) and the pause.
   - Do not add `run.jsonl` or the log files to git.

## Read the report

### Input and warm-up
- The input is synthetic Piper audio. It is cleaner than a real microphone. Real STT times and WER can be worse.
- The first turn of each run is a warm-up. It includes the moderation self-test (`selftest`) and the connection setup. The report leaves it out of all numbers.
- Each start of `latency_run` is a new run. A resumed run also loses its first turn to warm-up.
- Failed turns are not in the numbers. The report counts them by code in "Failures by code".

### Stage names and budget rows
The stage names describe what the code does today. The budget rows come from `docs/architecture.md`.

| Stage in the report | Budget row | Note |
|---|---|---|
| Upload tail | Upload tail + VAD end (0.2 s) | not measured: no device in the run |
| `vad` | Upload tail + VAD end (0.2 s) | server VAD end |
| `stt` | STT (1.2 s) | |
| `mod_transcript` | no budget row | moderation of the STT text |
| `chat_first_sentence` | LLM to first sentence (2.0 s) | passive: the pipeline waits for the full reply |
| `chat_full` | no budget row | the full chat reply |
| `mod_reply` | Moderation of sentence 1 (0.5 s) | covers the whole reply, not sentence 1 |
| `tts1` | TTS of sentence 1 + resample (0.8 s) | |
| `selftest` | no budget row | first turn only, so it is in the warm-up |
| Network + device buffer | 0.3 s | not measured: no device in the run |
| `latency_ms (total)` | p50 target 5 s | |

- "Missing" counts ok turns that have no time for the stage. For example, a reply with no first sentence has no chat and TTS times.
- The "not measured" rows are not in `latency_ms`. Remember this when you compare p50 with the 5 s target.
- The line under the table says if p50 `latency_ms` meets or misses the target, for each language.

### The streaming-saving line
- The line "Time per-sentence streaming could save" shows `chat_full - chat_first_sentence`.
- This is the time that per-sentence streaming could save. The pipeline does not stream sentences today.
- If p50 misses the target and this gap is large, streaming is the first fix to look at.

### WER
- WER compares the STT text with the phrase text. It is a side number. It does not fail the run.

## Resume after a rate limit
1. Stop the run with Ctrl+C if it gives many `rate_limited` codes.
2. Wait until the provider limit resets.
3. Run the same command again with `--resume` and a longer pause. Use the same `--out` folder.

   ```bash
   OTA_TTS_VOICE=<pl voice> server/.venv/bin/python -m ota_server.latency_run \
     --audio latency-audio --lang pl --out latency-audio/run-<date> --pause 15 --resume
   ```

- `--resume` skips the phrase ids of this language that are already in `run.jsonl`.
- A turn that ended with `rate_limited` is not done. The resumed run plays it again.
- If the stop damaged the last line of `run.jsonl`, the run drops that line and logs a warning.

## LiteLLM checklist (issue #20)
Do these checks once, with LiteLLM set up as in [litellm-fallback.md](litellm-fallback.md).
Run the `curl` commands on the server host. Use the virtual key of the role. In the examples, the proxy is `http://192.168.1.30:4000/v1`.
To make a tier fail, set its `api_base` in `config.yaml` to an address with no service, for example `http://127.0.0.1:9/v1`. Restart the proxy. Put the old value back after the check.

### Model ids
The model ids marked `# verify (issue #26)` in [`docs/examples/litellm/config.yaml`](../examples/litellm/config.yaml):

- [ ] `gemini/gemini-2.5-flash` (group `chat-gemini`). Send a chat request with `"model": "chat-gemini"`. Expected: a reply with a message.
- [ ] `mistral/voxtral-mini-latest` (group `voxtral-mini-latest`). Send a WAV to `/audio/transcriptions` with `model=voxtral-mini-latest`. Expected: JSON with a `text` field that matches the phrase.
- [ ] `openai/Systran/faster-whisper-small` (group `stt-local`). Send a WAV to `/audio/transcriptions` with `model=stt-local`. Expected: JSON with a `text` field that matches the phrase.
- [ ] `openai/tts-1` (group `tts`). Send text to `/audio/speech` with `"model": "tts"` and `"response_format": "wav"`. Expected: a WAV file (it starts with `RIFF`).
- [ ] `openai/speaches-ai/piper-pl_PL-gosia-medium` (group `tts-local`). Send text to `/audio/speech` with `"model": "tts-local"` and `"response_format": "wav"`. Expected: a WAV file with Polish speech.

Example STT request:

```bash
curl -s http://192.168.1.30:4000/v1/audio/transcriptions \
  -H "Authorization: Bearer <stt virtual key>" \
  -F file=@latency-audio/pl/<id>.wav -F model=stt-local
```

Example TTS request:

```bash
curl -s http://192.168.1.30:4000/v1/audio/speech \
  -H "Authorization: Bearer <tts virtual key>" -H "Content-Type: application/json" \
  -d '{"model": "tts-local", "voice": "pl_PL-gosia-medium", "input": "Dzień dobry", "response_format": "wav"}' \
  --output check.wav
head -c 4 check.wav
```

### STT fallback
- [ ] Make the `voxtral-mini-latest` group fail. Send a WAV to `/audio/transcriptions` with `model=voxtral-mini-latest`. Expected: JSON with `text` from `stt-local`, and the LiteLLM log shows a fallback to `stt-local`.

### TTS fallback
- [ ] Make the `tts` group fail. Send text to `/audio/speech` with `"model": "tts"`. Expected: a WAV file from `tts-local`, and the LiteLLM log shows a fallback to `tts-local`.

### Local model down (QA-012)
- [ ] Make the `mistral-small-latest` and `chat-gemini` groups fail. Stop Ollama. Speak one turn on the device. Expected: the turn ends with `llm_failed`, the same as without LiteLLM.

### speaches formats (QA-013)
- [ ] Send a WAV straight to speaches `/v1/audio/transcriptions`. Expected: JSON with a `text` field.
- [ ] Send text straight to speaches `/v1/audio/speech` with `"response_format": "wav"`. Expected: a WAV file (it starts with `RIFF`). Step 2 of this guide also uses this call.

### Record the result
- [ ] Write the result of each check in issue #20. For a failed check, add the error and the LiteLLM release tag.

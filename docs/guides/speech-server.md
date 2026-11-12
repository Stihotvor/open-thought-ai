# Local speech server (free)

This guide sets up a free, local speech server and connects it to the open-thought-ai server.
It costs nothing to run: no cloud account, no per-call price.

We use [speaches](https://github.com/speaches-ai/speaches) (MIT). It gives an OpenAI-compatible
API for speech to text (STT, faster-whisper) and text to speech (TTS).

Status: written from the speaches docs (checked 2026-11-12), not yet tested end to end with
our server. Items marked **verify** are not confirmed.

## 1. Pick the host
- A laptop or a home server on the same LAN as the open-thought-ai server.
- CPU is enough for `faster-whisper-small`. An NVIDIA GPU makes larger models fast.
- Give the host a fixed LAN IP (for example `192.168.1.20`) in your router.

## 2. Start speaches
1. Install Docker.
2. Pick a release tag on the [releases page](https://github.com/speaches-ai/speaches/releases).
   Use a fixed tag, not `latest`. The image names are
   `ghcr.io/speaches-ai/speaches:<tag>-cpu` and `ghcr.io/speaches-ai/speaches:<tag>-cuda`.
3. Make an API key. Our server sends a key to any endpoint that has its own base URL.

   ```bash
   openssl rand -hex 24
   ```

4. Start the container (CPU). Replace `<tag>` and `<key>`:

   ```bash
   docker run --detach --restart unless-stopped --name speaches \
     --publish 8000:8000 \
     --env API_KEY=<key> \
     --volume hf-hub-cache:/home/ubuntu/.cache/huggingface/hub \
     ghcr.io/speaches-ai/speaches:<tag>-cpu
   ```

   For an NVIDIA GPU, use the `-cuda` image and add `--gpus=all`.

5. Check that it runs: `curl http://192.168.1.20:8000/health`.

## 3. Download the models
speaches has a CLI that runs with `uvx` (part of uv):

```bash
uvx speaches-cli registry ls --task automatic-speech-recognition
uvx speaches-cli model download Systran/faster-whisper-small
```

- STT: `Systran/faster-whisper-small` handles EN, PL and UK. Our laptop test got all three
  right with one wrong word in UK. A larger model is more exact but slower on a CPU.
- TTS: the speaches docs show Kokoro, which has no Polish or Ukrainian voice. Piper has
  PL (`pl_PL-gosia-medium`) and UK (`uk_UA-lada-x_low`) voices. **Verify** that the speaches
  release you use serves Piper voices, and the exact model id it expects.
- If the CLI cannot reach the server, set its base URL first (**verify** the variable name with
  `uvx speaches-cli --help`).

## 4. Connect open-thought-ai
Add these lines to the server `.env`. The speech server is on the LAN over plain `http://`,
so the insecure flag is needed. It allows `http://` only to a private LAN IP.

> **Warning:** do not use voice mode with children yet. The server has no moderation yet,
> so nothing checks what is said to the child. Voice mode starts only when you accept this
> with `OTA_VOICE_WITHOUT_MODERATION=1`.

```bash
OTA_PIPELINE=voice
OTA_VOICE_WITHOUT_MODERATION=1
OTA_ALLOW_INSECURE_MODEL_ENDPOINT=1
OTA_STT_BASE_URL=http://192.168.1.20:8000/v1
OTA_STT_KEY=<key>
OTA_STT_MODEL=Systran/faster-whisper-small
OTA_STT_RATE=5
OTA_STT_BURST=2
```

For TTS, add the same four `OTA_TTS_*` lines with the Piper model id, once step 3 confirms it.
Chat can stay on Mistral (the default endpoint).

Rules the server checks at startup (docs/security.md):
- A role with its own base URL needs its own key.
- `http://` works only for loopback, or for a private IP with the flag above.
- Roles that share one endpoint and key must have the same `_RATE` and `_BURST`.

## 5. Test
1. Record a short WAV (16 kHz mono).
2. Send it straight to speaches:

   ```bash
   curl http://192.168.1.20:8000/v1/audio/transcriptions \
     -H "Authorization: Bearer <key>" \
     -F file=@test.wav -F model=Systran/faster-whisper-small -F language=pl
   ```

3. Restart the open-thought-ai server and speak a turn on the device.

## Notes
- Keep port 8000 closed to the internet. Only the LAN needs it.
- Parakeet (our fastest model in the laptop test) is not in the speaches docs. It needs a
  different server, for example a small onnx-asr wrapper. That is a possible later step.

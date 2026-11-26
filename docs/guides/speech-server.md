# Local speech server (free)

This guide sets up a free, local speech server and connects it to the open-thought-ai server.
It costs nothing to run: no cloud account, no per-call price.

We use [speaches](https://github.com/speaches-ai/speaches) (MIT). It gives an OpenAI-compatible
API for speech to text (STT, faster-whisper) and text to speech (TTS).

Status: tested with speaches 0.8.3 on 2026-11-26: Piper TTS and Whisper STT work over HTTP
(a Polish phrase went through TTS, then STT, and came back exact). Not yet tested with a device turn.

## 1. Pick the host
- A laptop or a home server on the same LAN as the open-thought-ai server.
- CPU is enough for `faster-whisper-small`. An NVIDIA GPU makes larger models fast.
- Give the host a fixed LAN IP (for example `192.168.1.20`) in your router.

## 2. Start speaches
1. Install Docker.
2. Pick a release tag on the [releases page](https://github.com/speaches-ai/speaches/releases).
   Use a fixed tag, not `latest`. The image tag has no `v`: release `v0.8.3` is image
   `ghcr.io/speaches-ai/speaches:0.8.3-cpu` (or `-cuda`).
3. Make an API key. Our server sends a key to any endpoint that has its own base URL.

   ```bash
   openssl rand -hex 24
   ```

4. Start the container (CPU). Replace `<tag>` and `<key>`:

   ```bash
   docker run --detach --restart unless-stopped --name speaches \
     --publish 8001:8000 \
     --env API_KEY=<key> \
     --volume hf-hub-cache:/home/ubuntu/.cache/huggingface/hub \
     ghcr.io/speaches-ai/speaches:<tag>-cpu
   ```

   For an NVIDIA GPU, use the `-cuda` image and add `--gpus=all`.

5. Check that it runs: `curl -H "Authorization: Bearer <key>" http://192.168.1.20:8001/health`.

## 3. Download the models
speaches has a CLI that runs with `uvx` (part of uv):

```bash
uvx speaches-cli registry ls --task automatic-speech-recognition
uvx speaches-cli model download Systran/faster-whisper-small
```

- STT: `Systran/faster-whisper-small` handles EN, PL and UK. Our laptop test got all three
  right with one wrong word in UK. A larger model is more exact but slower on a CPU.
- TTS: Piper voices work. Kokoro has no Polish or Ukrainian voice. The model id is
  `speaches-ai/piper-<voice>`, and the `voice` field is the short name. Download with the API:

  ```bash
  curl -X POST -H "Authorization: Bearer <key>" \
    http://192.168.1.20:8001/v1/models/speaches-ai/piper-en_US-amy-medium
  ```

  | Language | Model id | `OTA_TTS_VOICE` |
  |---|---|---|
  | EN | `speaches-ai/piper-en_US-amy-medium` | `amy` |
  | PL | `speaches-ai/piper-pl_PL-gosia-medium` | `gosia` |
  | UK | `speaches-ai/piper-uk_UA-lada-x_low` | `lada` |

  Find other voices with `GET /v1/registry?task=text-to-speech`.
- If the CLI cannot reach the server, set its base URL first (**verify** the variable name with
  `uvx speaches-cli --help`).

## 4. Connect open-thought-ai
Add these lines to the server `.env`. The speech server is on the LAN over plain `http://`,
so the insecure flag is needed. It allows `http://` only to a private LAN IP.

```bash
OTA_PIPELINE=voice
OTA_ALLOW_INSECURE_MODEL_ENDPOINT=1
OTA_STT_BASE_URL=http://192.168.1.20:8001/v1
OTA_STT_KEY=<key>
OTA_STT_MODEL=Systran/faster-whisper-small
OTA_STT_RATE=5
OTA_STT_BURST=2
```

For TTS, add these lines (EN voice shown). STT and TTS share one endpoint and key, so their
`_RATE` and `_BURST` must match:

```bash
OTA_TTS_BASE_URL=http://192.168.1.20:8001/v1
OTA_TTS_KEY=<key>
OTA_TTS_MODEL=speaches-ai/piper-en_US-amy-medium
OTA_TTS_VOICE=amy
OTA_TTS_RATE=5
OTA_TTS_BURST=2
```

Chat can stay on Mistral (the default endpoint).

Rules the server checks at startup (docs/security.md):
- A role with its own base URL needs its own key.
- `http://` works only for loopback, or for a private IP with the flag above.
- Roles that share one endpoint and key must have the same `_RATE` and `_BURST`.

## 5. Test
1. Record a short WAV (16 kHz mono).
2. Send it straight to speaches:

   ```bash
   curl http://192.168.1.20:8001/v1/audio/transcriptions \
     -H "Authorization: Bearer <key>" \
     -F file=@test.wav -F model=Systran/faster-whisper-small -F language=pl
   ```

3. Restart the open-thought-ai server and speak a turn on the device.

## Notes
- Port 8000 is the open-thought-ai server, so speaches uses 8001. Keep port 8001 closed to the internet. Only the LAN needs it.
- Parakeet (our fastest model in the laptop test) is not in the speaches docs. It needs a
  different server, for example a small onnx-asr wrapper. That is a possible later step.

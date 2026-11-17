# Provider fallback with LiteLLM

This guide connects the open-thought-ai server to your own LiteLLM proxy.
LiteLLM then moves a failed call to the next provider.
Chat goes from Mistral to Gemini, then to a local Ollama model.
STT and TTS go from the cloud to a local speaches server.

LiteLLM is not part of our stack ([ADR 0002](../adr/0002-litellm-optional.md)).
You run it on your own host. Without LiteLLM, the server has no fallback.

Status: STT/TTS fallback and the `# verify` model ids are untested, see issue #26.
The LiteLLM docs do not confirm fallback for the `/audio/*` routes.
Chat fallback uses standard LiteLLM features.

## Before you start
- A host on your LAN with Docker. Give it a fixed IP, for example `192.168.1.30`.
- A Mistral API key.
- A Gemini key (Google AI Studio). This is optional. See "No Gemini key" below.
- Ollama with a pulled model, for example `llama3.1:8b`. LM Studio also works.
- For STT and TTS fallback: a local speaches server. See [speech-server.md](speech-server.md).
- A Postgres database for LiteLLM. LiteLLM needs it for virtual keys.

## Setup steps

1. Set the proxy environment on the LiteLLM host. Put these names in the environment of the proxy, not in our `.env`: `MISTRAL_API_KEY`, `MISTRAL_API_BASE` (`https://api.mistral.ai/v1`), `GEMINI_API_KEY`, `OLLAMA_API_BASE` (for example `http://192.168.1.20:11434`), `SPEACHES_API_KEY`, `SPEACHES_API_BASE` (for example `http://192.168.1.20:8000/v1`), `CLOUD_TTS_API_KEY`, `CLOUD_TTS_API_BASE`, `LITELLM_MASTER_KEY` (make it with `openssl rand -hex 24`, start it with `sk-`) and `DATABASE_URL`.
2. Copy [`docs/examples/litellm/config.yaml`](../examples/litellm/config.yaml) to the LiteLLM host as `config.yaml`. Change the Ollama model id to the model that you pulled. Do not increase `num_retries` or `timeout`.
3. Start the proxy with a fixed LiteLLM release tag. For example: `docker run --detach --restart unless-stopped --name litellm --publish 192.168.1.30:4000:4000 --env-file litellm.env --volume ./config.yaml:/app/config.yaml ghcr.io/berriai/litellm:<tag> --config /app/config.yaml`. Then, on the LiteLLM host itself, make one virtual key for each role (chat, STT, TTS): `curl -s http://localhost:4000/key/generate -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H "Content-Type: application/json" -d '{"key_alias": "ota-chat"}'`. Do this again with `ota-stt` and `ota-tts`. Use the master key only on the LiteLLM host, so it never goes over the LAN.
4. Set the role variables in the `.env` of the open-thought-ai server. Use the LiteLLM URL and a different virtual key for each role. Keep `OTA_CHAT_MODEL` and `OTA_STT_MODEL` at their defaults. Set `OTA_TTS_MODEL=tts`. See the block below.
5. Check the proxy from the server host: `curl -s http://192.168.1.30:4000/v1/chat/completions -H "Authorization: Bearer <chat virtual key>" -H "Content-Type: application/json" -d '{"model": "mistral-small-latest", "messages": [{"role": "user", "content": "Say OK"}]}'`. The reply must contain a message.
6. Restart the server with `docker compose up --detach server`. Speak one turn to the device. Then read `docker compose logs server` and make sure that the turn has no error code.

The `.env` lines for step 4:

```bash
OTA_PIPELINE=voice
# LiteLLM runs on a private LAN IP over http://
OTA_ALLOW_INSECURE_MODEL_ENDPOINT=1

OTA_CHAT_BASE_URL=http://192.168.1.30:4000/v1
OTA_CHAT_KEY=<chat virtual key>
OTA_CHAT_MODEL=mistral-small-latest

OTA_STT_BASE_URL=http://192.168.1.30:4000/v1
OTA_STT_KEY=<stt virtual key>
OTA_STT_MODEL=voxtral-mini-latest

OTA_TTS_BASE_URL=http://192.168.1.30:4000/v1
OTA_TTS_KEY=<tts virtual key>
OTA_TTS_MODEL=tts

# Moderation stays direct to Mistral. Do not set OTA_MODERATION_BASE_URL.
OTA_MODEL_BASE_URL=https://api.mistral.ai/v1
OTA_MODEL_KEY=<Mistral API key>
OTA_MODERATION_MODEL=mistral-moderation-latest
```

## Notes

### No Gemini key (E1)
Remove Gemini from the chain. In `config.yaml`, delete the `chat-gemini` group under `model_list`.
Then delete `chat-gemini` from the chat fallback entry:

```yaml
    - mistral-small-latest: [chat-local]
```

You do not need `GEMINI_API_KEY`. The chain still ends at the local model.

### LM Studio and not Ollama (E2)
Change the `chat-local` group. Use the LM Studio provider prefix and your LM Studio model id:

```yaml
  - model_name: chat-local
    litellm_params:
      model: lm_studio/<model-id>
      api_base: os.environ/LM_STUDIO_API_BASE  # e.g. http://192.168.1.20:1234/v1
```

Set `LM_STUDIO_API_BASE` in the proxy environment. You do not need `OLLAMA_API_BASE`.

### Chat only through LiteLLM (E3)
You can send only chat through LiteLLM. Set only the `OTA_CHAT_*` lines from step 4.
Leave out the `OTA_STT_BASE_URL`, `OTA_STT_KEY`, `OTA_TTS_BASE_URL` and `OTA_TTS_KEY` lines.
STT and TTS then use the default endpoint (`OTA_MODEL_BASE_URL` and `OTA_MODEL_KEY`), or their own direct endpoint.
For a direct local speaches server, see [speech-server.md](speech-server.md).
In this split, set `OTA_TTS_MODEL` to the model id of your TTS endpoint, not to `tts`.

### Moderation
Moderation does not go through LiteLLM. The server sends it direct to Mistral.
The example config has no moderation group. Keep `OTA_MODERATION_BASE_URL` unset.

### LAN and http://
The server requires https, except for loopback.
For http:// to a LAN host, set `OTA_ALLOW_INSECURE_MODEL_ENDPOINT=1`.
The host must be a private IP (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` or `fc00::/7`).
Use the IP, not a host name. Otherwise the server does not start.

### Keys and rates
Keep `litellm.env` out of git and readable only by its owner (`chmod 600 litellm.env`). It holds the master key and the provider keys.
If the master key leaks, make a new one and new virtual keys.
http:// on the LAN sends the virtual keys in clear text. Use a trusted LAN, or put a TLS reverse proxy in front of LiteLLM.
A role with its own base URL must have its own key. The server does not start without it.
Use one LiteLLM virtual key for each role. Then you can revoke or limit one role and keep the others.
If roles share one key, give them the same `_RATE` and `_BURST` values.
For example, `OTA_CHAT_RATE=1` and `OTA_STT_RATE=1`.

### Turn deadline
Each turn has a deadline of 8 s.
The example config sets `num_retries: 0` and `timeout: 2`.
Three chat groups at 2 s each stay inside the 8 s.
If you increase these values, a turn can end before the fallback reaches the local model.

### Local model down
If every chat group fails, including the local model, the turn ends with `llm_failed`.
This is the same result as without LiteLLM. Start Ollama or LM Studio and try again.

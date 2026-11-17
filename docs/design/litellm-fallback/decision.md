# Decision — LiteLLM fallback example (issue #20)

## Context
Issue #20 asks for an optional LiteLLM config with a chat fallback Mistral → Gemini → Ollama/LM Studio and a local STT/TTS fallback (`requirements.md`). ADR 0002 keeps LiteLLM outside this stack. The server needs no code change.

## Options
- Path 1, one complete example: one `config.yaml`, one guide, one offline test. Effort S.
- Path 2, modular examples: 5 configs and 3 `.env` snippets, one guide, one parametrized test. Effort M. The files can drift apart.

## Decision
Path 1, chosen by the user at G1 on 2026-11-17, with the architect fixes:
- `docs/examples/litellm/config.yaml`. Model groups: `mistral-small-latest` (Mistral), `chat-gemini`, `chat-local` (Ollama), `voxtral-mini-latest` (Mistral STT), `stt-local` (speaches), `tts` (cloud TTS), `tts-local` (speaches). LiteLLM falls back between groups in list order, so each tier is its own group.
- `router_settings`: `num_retries: 0`, `timeout: 2`, fallbacks `mistral-small-latest → [chat-gemini, chat-local]`, `voxtral-mini-latest → [stt-local]`, `tts → [tts-local]`. 3 chat groups × 2 s stay inside the 8 s turn deadline.
- Every `api_key` and `api_base` is `os.environ/...`. No moderation group (T3).
- `docs/guides/litellm-fallback.md`: 6 setup steps. Notes for E1 (no Gemini), E2 (LM Studio), E3 (chat only), the LAN rule (`OTA_ALLOW_INSECURE_MODEL_ENDPOINT=1` and a private IP), one LiteLLM virtual key per role with its own base URL, and "STT/TTS fallback untested, see #26".
- `server/tests/test_litellm_example.py`: offline. It checks F1, F2, M1, M3, and a no-Gemini variant built inside the test. `pyyaml` and `types-PyYAML` become dev dependencies.

## Consequences
- The owner keeps `OTA_CHAT_MODEL` and `OTA_STT_MODEL` at their defaults. `OTA_TTS_MODEL` is `tts`.
- The test proves the structure, not LiteLLM behaviour. Provider prefixes and the speaches model ids stay unverified until issue #26 (F4).
- Fallback for `/audio/*` routes in LiteLLM is not confirmed in the docs. The guide states this.
- Given up: ready-made files for each variant (Path 2).

# Path 2 — Modular examples per role

Lens: small LiteLLM configs per use, each with a matching OTA env snippet. One guide and one parametrized offline test.

## Files (new)
- `examples/litellm/chat-fallback.yaml`, `audio-fallback.yaml`, `combined.yaml`
- `examples/litellm/chat-no-gemini.yaml` (E1), `chat-lmstudio.yaml` (E2)
- `examples/litellm/chat-fallback.env`, `audio-fallback.env`, `combined.env` (placeholders only)
- `docs/guides/litellm-fallback.md`
- `server/tests/test_litellm_examples.py`

## Config
Same shape in every file: groups `chat-mistral`, `chat-gemini`, `chat-local`, `stt`, `stt-local`, `tts`, `tts-local`. `router_settings`: `num_retries: 1`, `timeout: 6`, fallbacks `chat-mistral → [chat-gemini, chat-local]`, `stt → [stt-local]`, `tts → [tts-local]`. Every `api_key` is `os.environ/...`.

## Edge cases
E1 and E2 have their own files. E3: `chat-fallback.env` sets only `OTA_CHAT_*`.

## Test (offline)
Parametrized over every yaml: F1, F2, M3. M1 on `combined.yaml`. Env snippets: placeholders only, each `_BASE_URL` has a `_KEY`, roles match the yaml groups. The guide names every file and has ≤ 6 steps.

Effort: M (7 example files). Main risk: the files drift apart, and a fix to one file does not reach the others.

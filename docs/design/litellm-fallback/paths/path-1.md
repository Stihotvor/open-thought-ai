# Path 1 — One complete example

Lens: one LiteLLM config for chat, STT and TTS, one guide, one offline test.

## Files (new)
- `docs/examples/litellm/config.yaml`
- `docs/guides/litellm-fallback.md`
- `server/tests/test_litellm_example.py` (PyYAML as a dev dependency, if not present)

## Config
- Group `chat`: Mistral `mistral-small-latest`. Groups `chat-gemini` (Gemini, model name to verify) and `chat-local` (`ollama_chat/<model>`, `api_base: os.environ/LOCAL_LLM_BASE`).
- Group `stt`: Mistral `voxtral-mini-latest`. Group `stt-local`: speaches faster-whisper.
- Group `tts`: cloud TTS. Group `tts-local`: speaches (model id to verify).
- `router_settings`: `num_retries: 1`, `timeout: 6`, fallbacks `chat → [chat-gemini, chat-local]`, `stt → [stt-local]`, `tts → [tts-local]`.
- Every `api_key` is `os.environ/...`. No moderation group (T3).

## Edge cases
- E1: the guide says which deployment and fallback entry to delete. The test checks the reduced shape too.
- E2: one-line change of the local deployment (`openai/` prefix, LM Studio `/v1` URL).
- E3: the guide sets only `OTA_CHAT_*`. STT and TTS stay direct.

## Guide
Six steps: env vars, copy config, start the proxy, set `OTA_*` lines, curl check, restart and test a turn. Notes: 8 s deadline (F3), STT/TTS fallback untested (F4, issue #26), the same `_RATE`/`_BURST` for roles that share one key.

## Test (offline)
Groups exist. Fallback keys and targets are defined groups (F1). `api_key` values use `os.environ/` (F2). Chat chain is 3 in order (M1). STT and TTS fall back to speaches. `num_retries` ≤ 1, `timeout` ≤ 8 (M3). The no-Gemini variant stays valid.

Effort: S. Main risk: valid YAML that is wrong for LiteLLM (prefixes, `fallbacks` syntax, speaches ids). The test checks structure, not behaviour.

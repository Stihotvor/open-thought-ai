# QA cases: LiteLLM fallback example (issue #20)

### QA-001 Chat chain has 3 groups in order
- Covers: M1, B2
- Type: functional
- Given the example config `docs/examples/litellm/config.yaml` as shipped
- When the offline test reads the chat fallback rule for `mistral-small-latest`
- Then the chain is exactly 3 groups in order: `mistral-small-latest`, `chat-gemini`, `chat-local`

### QA-002 STT and TTS fallback groups on speaches
- Covers: M1, B3, T5
- Type: functional
- Given the example config as shipped
- When the offline test reads the fallbacks for `voxtral-mini-latest` and `tts`
- Then `voxtral-mini-latest` falls back to `stt-local` and `tts` falls back to `tts-local`, and both local groups point at the speaches base URL variable

### QA-003 No literal secret in the example
- Covers: M2, F2, T4
- Type: security
- Given the example config with 7 model groups
- When the offline test checks every `api_key` value
- Then 100 % of the values start with `os.environ/` and 0 are literal

### QA-004 Literal key is rejected
- Covers: F2
- Type: failure
- Given a copy of the config where `chat-gemini` has `api_key: sk-test-1234`
- When the offline test checks the copy
- Then the test fails and names `chat-gemini`

### QA-005 Unknown fallback target is rejected
- Covers: F1
- Type: failure
- Given a copy of the config where the chat chain lists `chat-gemin` (typo) instead of `chat-gemini`
- When the offline test checks the copy
- Then the test fails and names `chat-gemin` as not a defined model group

### QA-006 Retries and timeout stay inside the deadline
- Covers: M3, F3
- Type: performance
- Given the example config as shipped
- When the offline test reads `num_retries` and `timeout`
- Then `num_retries` is 0 (at most 1) and `timeout` 2 s times 3 chat groups is 6 s, at most 8 s

### QA-007 Timeout too high is rejected
- Covers: M3, F3
- Type: failure
- Given a copy of the config with `timeout: 4` and 3 chat groups (12 s)
- When the offline test checks the copy
- Then the test fails because 12 s exceeds the 8 s turn deadline

### QA-008 No-Gemini variant still ends at the local model
- Covers: E1
- Type: edge
- Given the config with `chat-gemini` removed from the model list and from the chat chain
- When the offline test checks the variant
- Then the chain is `mistral-small-latest` then `chat-local`, and every target is a defined group

### QA-009 Guide has 6 setup steps
- Covers: M4, B1
- Type: functional
- Given `docs/guides/litellm-fallback.md`
- When a reader counts the numbered setup steps from "LiteLLM running" to "OTA uses it"
- Then there are exactly 6 steps and no 7th

### QA-010 Guide covers LM Studio change
- Covers: E2
- Type: edge
- Given an owner who runs LM Studio and not Ollama
- When the owner reads the guide section for the local model
- Then the guide shows a one-line change of the `chat-local` deployment

### QA-011 Guide shows chat-only split
- Covers: E3
- Type: edge
- Given an owner who wants only chat through LiteLLM
- When the owner reads the guide
- Then the guide lists the chat role base URL and key variables for LiteLLM, and says STT and TTS keep their direct values

### QA-012 Local model down ends the turn with llm_failed
- Covers: E4, B2
- Type: failure
- Given Mistral and Gemini both fail and Ollama is stopped
- When the device sends one chat turn
- Then the turn ends with `llm_failed`, as without LiteLLM
- OPEN: the issue has only an offline test, so this case needs a manual run with real services

### QA-013 Local STT and TTS accept the server formats
- Covers: E5
- Type: functional
- Given a speaches server set up as in `docs/guides/speech-server.md`
- When the server requests TTS with `response_format: wav` and STT with a JSON `text` reply
- Then both calls succeed on the local fallback
- OPEN: the test is offline, so a real check waits for issue #26

### QA-014 Guide marks STT/TTS fallback untested and states the deadline
- Covers: F4, F3
- Type: functional
- Given `docs/guides/litellm-fallback.md`
- When a reader searches for `#26` and `8 s`
- Then the guide says STT/TTS fallback is untested, refers to #26, and states the 8 s turn deadline

### QA-015 No moderation group and no compose change
- Covers: T1, T3
- Type: security
- Given the example config and `compose.yaml`
- When a reviewer checks both files
- Then the config has 0 moderation groups, and `compose.yaml` has 0 changes and no LiteLLM service

### QA-016 Existing checks stay green
- Covers: M5, T2
- Type: functional
- Given the branch with the new test, the config, and the 2 new dev dependencies
- When pytest, ruff, and mypy run in `server`
- Then all 3 report 0 errors and 0 files change in the server source folder

### QA-017 LAN rule is documented
- Covers: B1, T7
- Type: edge
- Given an owner whose LiteLLM runs on a private LAN address such as 192.168.1.50
- When the owner reads the guide
- Then the guide says to set `OTA_ALLOW_INSECURE_MODEL_ENDPOINT=1`, to use one virtual key per role, and to keep `OTA_CHAT_MODEL` at `mistral-small-latest`

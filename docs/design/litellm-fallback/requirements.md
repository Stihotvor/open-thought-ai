# Requirements — LiteLLM fallback example (issue #20)

User choices (2026-11-17): an example config and a guide, no compose change. speaches is the local STT/TTS. Moderation goes direct to Mistral. Offline test only.

## Business Goals
- B1: An owner who wants provider fallback can copy one example LiteLLM config and point the OTA roles at it in 15 minutes or less, with the guide only.
- B2: When Mistral fails or rate-limits chat, the turn continues on Gemini, then on a local Ollama or LM Studio model. The device user sees a reply, not `llm_failed`.
- B3: When the cloud STT or TTS fails, the turn continues on a local speaches server. Cost stays 0 €.
- B4: Close issue #20 for milestone M3.

## Technical Constraints
- T1: ADR 0002 holds. LiteLLM runs outside this stack. `compose.yaml` does not change.
- T2: No server code change is needed or allowed for this issue. The server already takes `OTA_<ROLE>_BASE_URL` and `OTA_<ROLE>_KEY` per role (`server/src/ota_server/endpoints.py`).
- T3: Moderation stays direct to Mistral (`OTA_MODERATION_BASE_URL`). It is fail-closed, and LiteLLM support for Mistral moderation is not confirmed.
- T4: The example config reads every secret from environment variables (`os.environ/...`). No key appears in the repo.
- T5: The STT/TTS fallback uses speaches, with the setup from `docs/guides/speech-server.md`.
- T6: Budget 0 €. Tests make no network call. A pytest checks the example config offline.
- T7: The first group of each role has the model name of the server default in `.env.example` (chat `mistral-small-latest`, STT `voxtral-mini-latest`), so `OTA_<ROLE>_MODEL` does not change. The proxy routes on the request `model` field.

## Edge Cases
- E1: The owner has no Gemini key. The guide says how to remove Gemini from the chain, and the chain still ends at the local model.
- E2: The owner uses LM Studio and not Ollama. The guide shows the one-line change of the local deployment.
- E3: Only chat goes through LiteLLM, and STT and TTS stay direct. The guide shows the role variables for this split.
- E4: The local model is not running. The turn ends with `llm_failed`, as without LiteLLM.
- E5: The server sends `response_format: wav` for TTS and expects JSON `text` from STT. The local fallback must accept both (speaches does).

## Failure Modes
- F1: A wrong group name in `fallbacks` makes LiteLLM skip the fallback. The offline test fails when a fallback target is not a defined model group.
- F2: A literal key in the example would leak a secret. The offline test fails when an `api_key` value does not start with `os.environ/`.
- F3: LiteLLM retries plus OTA retries can exceed the 8 s turn deadline. The example sets `num_retries` and `timeout` low, and the guide states the deadline.
- F4: STT/TTS fallback is not verified against real providers in this issue (user choice). The guide marks it untested. OPEN: accepted risk, real check in issue #26.

## Success Metrics
- M1: The offline test passes: a chat fallback chain of exactly 3 model groups in order Mistral → Gemini → local, and an STT and a TTS fallback group on speaches.
- M2: 0 literal secrets in the example: 100 % of `api_key` values use `os.environ/`.
- M3: `num_retries` ≤ 1, and `timeout` × chat groups ≤ 8 s in the example.
- M4: The guide has ≤ 6 setup steps from "LiteLLM running" to "OTA uses it".
- M5: Existing checks stay green: pytest, ruff, mypy with 0 errors.

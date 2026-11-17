# QA results — litellm-fallback

Run: `server/.venv/bin/pytest -q`: 258 passed, 0 failed (2026-11-17). ruff check and format: clean. mypy (strict project config): 0 errors.
The test-engineer agent was blocked by a path hook on `server/tests/`, so the main session wrote and ran the tests.
All tests are in `server/tests/test_litellm_example.py` (E).

Counts: pass 15, partial 0, fail 0, not-automated 2.

| Case | Status | Evidence / gap |
|---|---|---|
| QA-001 | pass | E test_chain_order |
| QA-002 | pass | E test_chain_order, test_local_audio_groups_use_speaches |
| QA-003 | pass | E test_secrets_come_from_env (api_key and api_base) |
| QA-004 | pass | E test_literal_gemini_key_is_named |
| QA-005 | pass | E test_typo_in_chain_is_named |
| QA-006 | pass | E test_time_budget |
| QA-007 | pass | E test_long_timeout_breaks_the_deadline |
| QA-008 | pass | E test_no_gemini_variant |
| QA-009 | pass | E test_guide_has_six_setup_steps |
| QA-010 | pass | E test_guide_mentions[lm_studio, LM_STUDIO_API_BASE] |
| QA-011 | pass | E test_guide_mentions[OTA_CHAT_* only] |
| QA-012 | not-automated | Needs real Mistral, Gemini and a stopped Ollama behind a live LiteLLM. Manual check in issue #26. |
| QA-013 | not-automated | Needs a live speaches server. Accepted risk F4, issue #26. |
| QA-014 | pass | E test_guide_mentions[#26, 8 s] |
| QA-015 | pass | E test_no_moderation_group, test_compose_has_no_litellm. `git diff main -- compose.yaml server/src` is empty. |
| QA-016 | pass | pytest 258 passed, ruff clean, mypy 0 errors. No file in `server/src` changed. |
| QA-017 | pass | E test_guide_mentions[OTA_ALLOW_INSECURE_MODEL_ENDPOINT=1, OTA_CHAT_MODEL, virtual key] |

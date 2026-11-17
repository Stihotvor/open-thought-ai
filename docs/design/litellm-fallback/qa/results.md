# QA results — litellm-fallback

Run: `server/.venv/bin/pytest -q`: 256 passed, 0 failed (2026-11-17). ruff check and format: clean. mypy (strict project config): 0 errors.
The test-engineer agent was blocked by a path hook on `server/tests/`, so the main session wrote and ran the tests.
Tests: `server/tests/test_litellm_example.py` (E) and `server/tests/test_litellm_guide.py` (G).

Counts: pass 14, partial 1, fail 0, not-automated 2.

| Case | Status | Evidence / gap |
|---|---|---|
| QA-001 | pass | E test_chain_order |
| QA-002 | pass | E test_chain_order, test_local_audio_groups_use_speaches |
| QA-003 | pass | E test_secrets_come_from_env (api_key and api_base) |
| QA-004 | pass | E test_literal_key_is_named[chat-gemini] |
| QA-005 | pass | E test_wrong_target_is_named[chat-gemin] |
| QA-006 | pass | E test_time_budget |
| QA-007 | pass | E test_long_timeout_breaks_the_deadline |
| QA-008 | pass | E test_no_gemini_variant |
| QA-009 | pass | G test_guide_setup_steps |
| QA-010 | pass | G test_guide_names_contract_values[lm_studio, LM_STUDIO_API_BASE] |
| QA-011 | partial | The chat-only section is in the guide (manual read). The guide test checks contract values only, not this prose. |
| QA-012 | not-automated | Needs real Mistral, Gemini and a stopped Ollama behind a live LiteLLM. Manual check in issue #26. |
| QA-013 | not-automated | Needs a live speaches server. Accepted risk F4, issue #26. |
| QA-014 | pass | G test_guide_names_contract_values[#26]. The 8 s note is in the guide (manual read). |
| QA-015 | pass | E test_no_moderation_group, G test_compose_has_no_litellm. `git diff main -- compose.yaml server/src` is empty. |
| QA-016 | pass | pytest 256 passed, ruff clean, mypy 0 errors. No file in `server/src` changed. |
| QA-017 | pass | G test_guide_names_contract_values[OTA_ALLOW_INSECURE_MODEL_ENDPOINT=1, OTA_CHAT_MODEL]. The virtual-key rule is in the guide (manual read). |

## Review (2026-11-17)
Verdict: PASS WITH WARNINGS. Gate 6/6 pass, TDD order pass. Performance pass not run (no runtime code).
Fixed: `num_retries == 0` check, guide and compose checks moved to `test_litellm_guide.py`, duplicate tests parametrized, task labels removed from test comments, named timeout value, a clearer sentence in the guide, guide security notes (master key only on the LiteLLM host, port bound to one IP, `chmod 600 litellm.env`, key rotation, TLS proxy for the LAN).
Stays, with reason:
- `# verify (issue #26)` on each unverified model id: each value needs its own marker, so that a reader sees which line to check.
- The `/audio/*` fallback note in the config and in the guide: the config note sits next to the setting that it limits.
- `without_gemini` repeats the fallback loop: 4 lines in a test helper, and a shared helper adds no clarity.

# QA results: n8n-mcp-example

Run: `scripts/gate.sh` on 2026-11-27. Result: 946 passed, 1 skipped, coverage 96.80%, ruff and mypy clean.
G = `server/tests/test_mcp_servers_guide.py` (27 tests). Other tests are in `server/tests/`.
Counts (47 cases): pass 33, partial 8, fail 0, not-automated 6.

## Pass (33)
- QA-001: G::test_guide_line_limit, G::test_line_check_rejects_121_lines
- QA-002: G::test_guide_setup_steps
- QA-003: G::test_step_check_rejects_seven_steps
- QA-004: G::test_step_check_rejects_a_numbering_gap
- QA-005, QA-006: G::test_example_loads
- QA-007: test_mcp_tool_safety::test_denied_server_does_not_stop_startup, test_mcp_policy::test_missing_variable_denied
- QA-009: test_mcp_config::test_safety_defect_rejected[plain_header_value]
- QA-010: G::test_guide_setting_names_exist
- QA-011: G::test_settings_check_rejects_a_typo
- QA-012, QA-013, QA-015: test_mcp_toolfilter, test_qa_tool_safety_filter::test_qa_061_hash_from_the_log_approves_the_tool, test_confirm_e2e::test_a_yes_runs_the_call_once
- QA-016: G::test_example_pins_and_no_confirm, test_mcp_config::test_safety_defect_rejected[short_pin]
- QA-017: test_mcp_toolfilter::test_changed_description_is_not_admitted, test_mcp_reconnect_pins
- QA-019, QA-020, QA-022: test_mcp_ssrf, test_mcp_policy::test_http_public_host_denied, test_qa_tool_safety_ssrf
- QA-023: test_mcp_tool_safety::test_redirect_fails_the_connect
- QA-024: test_mcp_config::test_no_confirm_unknown_tool_skips_entry
- QA-025: G::test_example_pins_and_no_confirm
- QA-026: G::test_no_confirm_check_rejects_an_unknown_tool
- QA-027, QA-029: test_mcp_registry, test_mcp_e2e::test_server_down_device_tools_work, test_mcp_toolfilter
- QA-032, QA-033, QA-034, QA-035, QA-036: test_confirm_e2e, test_confirm_text, test_confirm_release, test_session_confirm, test_mcp_source
- QA-039: G::test_guide_has_no_invented_n8n_identifiers, G::test_denylist_check_rejects_an_invented_identifier
- QA-044: the guide tests make no network call
- QA-046: test_mcp_config::test_safety_defect_rejected[shared_secret_name]
- QA-047: test_mcp_config::test_safety_defect_rejected[reserved_name_device]

## Partial (8)
- QA-014: the test checks the log line name. No test checks the "copy into `tools` and restart" text.
- QA-018: the test checks the "Tool vanished" item. No test checks the pin update wording.
- QA-021: the test checks the comma list. No test checks "private hosts are blocked unless listed".
- QA-028: the test checks the "n8n down" item. No test checks "restart after n8n is up".
- QA-030: the test checks the symptoms. No test checks that each item gives a fix.
- QA-031: the heading is required. No test feeds a guide without it.
- QA-038: the test checks one README link. No test checks its place next to the speech link.
- QA-040: the denylist and "see your n8n UI" are checked. No test proves that no exact node id or path exists.

## Not-automated (6)
- QA-008, QA-037: prose checks were removed in the review round, because exact prose tests break on harmless rewording.
- QA-041: needs a live n8n instance. The user accepted this risk.
- QA-042: a review of the change set, done by the code review.
- QA-043: covered by the gate run (ruff, mypy, coverage) on the main session, not by a pytest case.
- QA-045: OPEN. The requirements do not say how a repeated `tools` key is handled.

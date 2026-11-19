# QA results: mcp-client

Tests: /home/yevhend/claude-projects/open-thought-ai/server/tests/test_qa_mcp_client.py (helpers: mcp_qa_stdio_server.py, mcp_qa_raw_server.py; reuses mcp_fakes.assert_pid_gone and free_port). Run: 47 passed, 1 skipped. Full suite with coverage: 594 passed, 1 skipped, coverage 98.61 %.

- QA-001 pass
- QA-002 pass
- QA-003 pass
- QA-004 pass
- QA-005 pass
- QA-006 pass
- QA-007 pass
- QA-008 pass
- QA-009 pass
- QA-010 pass
- QA-011 pass
- QA-012 pass
- QA-013 pass
- QA-014 pass
- QA-015 pass
- QA-016 pass (the config loader drops "bad.tool" and logs the error; the source-level name check is covered by QA-017)
- QA-017 pass
- QA-018 pass
- QA-019 pass
- QA-020 pass
- QA-021 pass
- QA-022 pass
- QA-023 pass
- QA-024 pass
- QA-025 pass
- QA-026 pass
- QA-027 pass (shutdown took about 4.6 s)
- QA-028 pass (error outcome code mcp_error; malformed `result.content` from a raw stdio server)
- QA-029 pass
- QA-030 pass
- QA-031 pass
- QA-032 pass
- QA-033 pass
- QA-034 pass
- QA-035 pass
- QA-036 pass
- QA-037 pass
- QA-038 pass
- QA-039 pass
- QA-040 pass
- QA-041 pass
- QA-042 pass
- QA-043 pass
- QA-044 pass
- QA-045 pass: the main session ran `server/scripts/gate.sh`: ruff check and format 0 findings, mypy 0 errors in 72 files, 594 passed and 1 skipped, coverage 96 % (fail_under 90). The pytest test is a skip marker, because the gate cannot run inside pytest.
- QA-046 pass (pyproject "mcp==1.30.0" and uv.lock version 1.30.0)
- QA-047 partial: an autouse guard in the QA file refuses any non-loopback inet connect and a test proves it; the real outbound-blocked full-suite run was not done.
- QA-048 pass

Counts: pass 46, partial 1 (QA-047), fail 0, not-automated 0.

# Review — external MCP client (issue #28)

Scope: `main...feat/28-mcp-client`. Verdict: PASS WITH WARNINGS.

## Layer 1 gate
- First run: FAIL (structure, `tests/test_qa_mcp_client.py` had 968 lines, limit 500). Fixed in `04b3302`.
- Second run: PASS (lint, complexity, types, security, structure, tests). TDD order check: pass.

## Fixed findings
- Medium (structure, security): `registry.py` ran `tools/list` outside the connect deadline, and `list_tools` had no page cap. Fixed in `543e338`: one deadline covers connect and build, at most 20 pages.
- Medium (structure, style): `TOOL_NAME` came from `hub.hub`. It now lives in `hub/types.py`.
- Medium/low (style, structure): two `ALLOW_ALL` defaults. Now one in `mcp/hooks.py`.
- Low (security): `source.py` logged server-controlled error text. It now logs only the tool name and the error type.
- Low (structure): `McpConnection.close` now documents that it must be idempotent.
- Low (style): the `hooks.py` docstring no longer names an issue number.

## Warnings that stay, with reasons
- Medium (structure): `adapters.py` holds lifecycle, reconnect and error classification. It is one responsibility, the SDK connection lifecycle. D2 keeps all SDK code in this one file, so a split would spread the SDK.
- Medium (style): comments cite design codes (D7, E7). The hub code from #27 uses the same convention (`hub.py`, D4a), and `decision.md` resolves each code.
- Medium (style): reconnect tests read private attributes. These are white-box concurrency tests for the lock and generation rules. Behaviour alone cannot show the race windows.
- Medium (security): tool descriptions and schemas from a server reach the LLM unfiltered. The hub `wrap()` step marks results as untrusted. Description caps and hash re-approval are #31 scope (`docs/security.md`).
- Low (security): plain `http://` is accepted, and a stdio `env` can hold a key in the servers file. HTTPS rules, redirects and `MCP_SECRET_*` are #31 scope.
- Low (performance): no cap on calls in flight, and no cool-down after a failed reconnect. Each call has a timeout of at most 60 s, and one device uses the server. Fixed in #77 (decision D13).
- Low (style): magic numbers and small duplication in test helpers.
- Low (structure): the app lifespan passes the default policy and filter. #31 adds the injection point.

## SDLC checks
- QA: 46 pass, 1 partial (QA-047, the loopback guard is tested, but no run had the network really blocked), 0 fail, 0 not-automated.
- Tasks: 6 of 6 done.

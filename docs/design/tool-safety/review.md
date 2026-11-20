# Review — tool safety (issue #31)

Range: `42d0651..HEAD` (branch `feat/31-tool-safety`). 9 source files and 22 test files changed.

## Verdict
PASS WITH WARNINGS. No critical or high finding. The fixed warnings are listed below. The remaining warnings stay for the reasons given.

## Layer 1: gate
- The quality gate passed 6 of 6 checks: lint, complexity, types, security, structure and tests. The TDD order check passed for 8 source commits.
- The `.venv` tools ran again after the fixes: ruff 0 findings, format clean, mypy 0 errors in 89 files, 749 passed and 1 skipped, coverage 96 %.
- `scripts/gate.sh` could not run in the sandbox, because `uv` needs a cache that can be written. CI runs it.

## Fixed after the review
- Medium (security), `mcp/ssrf.py`: CGNAT `100.64/10`, NAT64 `64:ff9b::/96` and other non-global addresses passed the check. Now every address that is not global is denied, and NAT64 and 6to4 addresses are unwrapped first.
- Low (security, performance), `mcp/ssrf.py`: DNS resolution had no timeout. Now it has a 3 s timeout, and a timeout is a deny.
- Low (performance), `mcp/toolfilter.py`: the schema was serialized twice. Now it is serialized once.
- Low (security), `mcp/config.py`: the stdio env check now also rejects `PASSWD`, `CRED`, `COOKIE` and `PRIVATE`.
- Medium (style), `mcp/config.py`: `_try_entry` no longer changes the `used` map. `load_servers` owns that update.
- Low (style): the `before_connect` docstring is correct now, and `RESERVED_NAMES` has a comment.

## Warnings that stay
- Secret headers go over `http://` to an allowlisted host. Requirement T5 allows this on purpose, for example for the n8n host on the LAN. The owner controls the allowlist.
- DNS rebinding is an accepted risk (`decision.md`). The follow-up is an httpcore backend that connects only to the checked IP.
- `adapters.py` holds the redirect guard and the connection lifecycle together. Decision D6 puts the guard there. A split is optional.
- `model_env()` gives the policy the full merged environment. The policy reads only the `MCP_SECRET_*` names in `headers`, so no value leaks.
- `mcp.shared._httpx_utils` is a private SDK import. The SDK is pinned to 1.30.0, and a comment marks the import for a recheck when the SDK version changes.
- The secret check on stdio env uses key names only. A secret in `args` is not found. The servers file is written by the owner, and `docs/security.md` says that secrets go only through `headers`.
- Low style items: design codes in comments, the `_redirect_in` loop and the parameter type of `_keep_specs`. They do not change behavior.

## QA
89 pass, 3 partial (QA-044, QA-055, QA-070), 0 fail, 4 not automated (QA-022, QA-094, QA-095, QA-096). Details: `qa/results.md`.

## Architecture review (PR #66, in place of CodeRabbit)
- Medium, fixed: pins were checked only at startup. Now each reconnect lists the tools again and runs the tool filter before the new session serves a call. A changed or missing admitted tool denies the reconnect, and the call returns an `mcp_error` outcome. Tests: `test_mcp_reconnect_pins.py`.
- Low, stays (follow-up): `policy.py` repeats the allowlist test from `ssrf.py`. One shared helper would hold the http rule.
- Low, stays (follow-up): the policy reads the environment once at startup. A rotated `MCP_SECRET_*` value needs a restart.
- Low, stays (follow-up): a source that the hub skips keeps its connection until shutdown.
- Low, stays (follow-up): `mcp.config` imports `hub.types`, and `hub.hub` re-exports `TOOL_NAME`. No cycle.

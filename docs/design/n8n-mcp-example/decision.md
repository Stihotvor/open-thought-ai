# Decision: MCP server guide with n8n example (issue #29)

## Context
The MCP client, confirm flow and tool safety are merged. Issue #29 asks for an n8n example. The user decided on 2026-11-27: write a guide only, with no workflow JSON, because the code works with any MCP server. See `requirements.md`.

## Options
- Path 1: a standalone guide with an offline test that loads its JSON through the real loader.
- Path 2: a new section in `docs/security.md` and a README link, with no test.

## Decision
The user chose Path 1 at gate G1 on 2026-11-27.
- Add `docs/guides/mcp-servers.md` (at most 120 lines, at most 6 numbered setup steps) and one README link next to the speech guide.
- Add `server/tests/test_mcp_servers_guide.py`. It runs offline. It loads the first json block through `load_servers` with a tmp file and `MCP_SECRET_*` set. It asserts one server and its field values.
- The test also checks: step numbers, line limit, `OTA_*` and `MCP_SECRET_*` names against `config.py` and `mcp/config.py`, pin format, `no_confirm` keys inside `tools`, and the strings for troubleshooting and for "see your n8n UI". It fails on invented n8n identifiers (small denylist).
- The guide marks the node type id, the URL path and the workflow JSON shape as "see your n8n UI".

## Consequences
- CI breaks when a setting name in the guide drifts from the code.
- The test checks names and the loader. It cannot prove the n8n facts. No live n8n check is possible. The user accepted this risk.
- Follow-up: `docs/design/mcp-client/decision.md:29` says `tools` is a list. The code uses a dict of pins. Fix that record in a separate change.

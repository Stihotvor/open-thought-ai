# Requirements: MCP server guide with n8n example (issue #29)

Source: GitHub issue #29, `docs/delivery-plan.md:74`, `docs/security.md:33-57`.
The user decided on 2026-11-27: deliver a guide only. Ship no n8n workflow JSON.

## Business Goals
- An owner connects an n8n MCP Server Trigger to the hub. The owner then calls one n8n tool by voice and hears a confirm question for a risky tool. This is the M4 goal "one n8n MCP tool work, risky tool asks to confirm".
- The guide names n8n as the worked example. The text states that any MCP server works the same way. The code has no n8n logic.
- Why now: the MCP client (#64), confirm flow (#30) and tool safety (#66) are merged. Issue #29 is the last M4 task.

## Technical Constraints
- Write one guide: `docs/guides/mcp-servers.md`. Add a README link next to the speech guide (`README.md:18`).
- Use the real config: `OTA_MCP_SERVERS_FILE`, `OTA_MCP_ALLOWED_HOSTS`, `MCP_SECRET_*` headers, the `tools` dict of sha256 pins, `no_confirm` (`server/src/ota_server/mcp/config.py`).
- State only n8n facts that the n8n docs confirm: SSE and streamable HTTP, auth none, bearer, header or OAuth2, tools attach through the Custom n8n Workflow Tool node. Mark the node type id, the URL path and the JSON shape as "see your n8n UI". Do not invent them.
- Setup steps are at most 6 and numbered. Do not add code, JSON workflow files or new settings.
- Fix the stale `docs/design/mcp-client/decision.md:29` only if it is in scope: it is a design record, so leave it. Name it as a follow-up if needed.
- Follow ADR rules and STE style (short sentences, one term per thing).

## Edge Cases
- The server returns a tool whose pin is missing or wrong. The tool is not admitted. The guide shows where the owner copies the hash (the WARNING log line `current hash sha256:...`).
- The owner edits a tool description in n8n. The hash changes and the tool is dropped until the owner updates the pin. The guide says this.
- The n8n host is a private or LAN address. SSRF checks block it unless the host is in `OTA_MCP_ALLOWED_HOSTS`.
- A tool is listed in `no_confirm` but is not a key of `tools`. Config load fails (`mcp/config.py:237-244`). The guide shows a valid example.

## Failure Modes
- n8n is down at start: the registry connects with a 5 s deadline, so the hub starts without the n8n tools (`mcp/registry.py:24`). The guide says what the owner sees: the tool is missing, not an error for the user.
- The bearer secret is missing in the environment: the guide tells the owner to set `MCP_SECRET_<NAME>` and never to put the secret in the servers file.
- The owner skips the pin step: the tool is silent and unavailable. The guide has a "Troubleshooting" list with each symptom and its fix.

## Success Metrics
- `server/tests/test_mcp_servers_guide.py` passes offline: it checks that every setting name and env name in the guide exists in `server/src/ota_server/config.py` and `mcp/config.py`, and that the setup steps are numbered 1 to at most 6.
- The JSON example in the guide loads with the real config loader without error (test parses the guide block and calls the loader).
- Gate passes: ruff, strict mypy, pytest with coverage at least 90 percent (`server/scripts/gate.sh`).
- The guide is at most 120 lines and holds zero invented n8n identifiers.
- OPEN: no live n8n check is possible (no instance). The user accepted this risk by choosing "guide only".

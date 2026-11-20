# Requirements — external MCP client (issue #28)

Second issue of milestone M4 (MCP hub). Issue #27 built the hub with `ToolSource` (`server/src/ota_server/hub/types.py`) and the device source. This issue adds external MCP servers as tool sources. #29 (n8n example), #30 (`confirm`) and #31 (tool safety) are out of scope, but the client must leave a seam for #31.

## Business Goals
- B1: The owner can connect external MCP servers over Streamable HTTP, SSE and stdio. The LLM can call their tools in a voice turn, like the device tools.
- B2: The external client plugs into the hub as a `ToolSource`. The pipeline and the hub chain do not change.
- B3: #29 can add the n8n MCP Server Trigger with config only, and #31 can add SSRF, secret-header and hash checks without a rewrite of the client.

## Technical Constraints
- T1: Python 3.12 FastAPI server in `server/`, ruff, strict mypy, coverage >= 90 %. Budget 0 €.
- T2: Use the official MCP Python SDK (`mcp` on PyPI), pinned to one version in `pyproject.toml` and `uv.lock`.
- T3: The owner lists servers in a JSON file. `OTA_MCP_SERVERS_FILE` points to it. No file or no variable means no external servers. Each entry has `name`, `transport` (`http`, `sse` or `stdio`), `url` or `command`/`args`/`env`, `timeout` (seconds) and `tools` (the allowlist of tool names to enable).
- T4: Only allowlisted tools are visible to the LLM. Unlisted tools stay hidden and calls to them are unknown tools. (Owner decision. #31 adds hash re-approval later.)
- T5: Tool names are `<server>__<tool>` and must pass the hub check D13 (`^[a-zA-Z0-9_-]{1,64}$`, no `__` in the server name).
- T6: Connections are app-wide. The server connects once at startup, shares the connections across device sessions, and reconnects after an error. One stdio child runs per stdio server. (Owner decision.)
- T7: A stdio child gets a clean environment: the MCP SDK default safe set (`HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, `USER`) and the variables in its `env` entry. It never gets model keys, `OTA_*` or `MCP_SECRET_*` values (`docs/security.md`).
- T8: The JSON file holds no secret values. #31 adds `MCP_SECRET_*` headers.
- T9: Tests use local fake MCP servers for each transport (in-process HTTP and SSE on 127.0.0.1, a stdio child from the test folder). No external service and no network outside loopback.
- T10: CI runs `server/scripts/gate.sh` (ruff, mypy, pytest) and must pass.

## Edge Cases
- E1: A configured server is down at startup: the server starts, logs a warning, and the tools of that server are not listed. The device tools keep working.
- E2: The server lists a tool that is not in the allowlist: the tool is hidden.
- E3: The allowlist names a tool that the server does not have: the server logs a warning and ignores the name.
- E4: The MCP tool returns `isError=true`: the hub returns an error outcome to the LLM with a short generic text, not the raw body.
- E5: The MCP tool returns non-text content (image, resource): the client keeps text parts and replaces other parts with a short placeholder.
- E6: The config file is invalid JSON or has a bad entry: that entry is skipped with a logged error. A duplicate server name or a name that fails D13 is rejected.
- E7: Two device sessions call the same external tool at the same time: both calls run on the shared connection and both get their own result.

## Failure Modes
- F1: A call takes longer than the source `timeout`: the call is cancelled and the LLM gets a timeout error. The hub outer guard (D4a) stays a backstop.
- F2: The connection drops between turns: the next call tries one reconnect. If it fails, the LLM gets an error outcome and the session survives.
- F3: A stdio child exits: the same as F2, with one restart per call.
- F4: Server shutdown closes all MCP sessions and stops all stdio children. No orphan process remains.
- F5: Any SDK exception inside `call` becomes an error `ToolOutcome`. `McpHub.run` never raises (#27 contract).

## Success Metrics
- M1: For each of the 3 transports, a test lists the allowlisted tools of a fake server and calls one tool with the correct result: 3 of 3 pass.
- M2: The timeout test returns an error within `timeout + 0.5 s`.
- M3: The reconnect test passes after the fake server restarts: 1 reconnect, 1 successful call.
- M4: After shutdown, 0 stdio child processes remain.
- M5: `gate.sh` passes: ruff 0 findings, mypy 0 errors, all tests pass, coverage >= 90 %.

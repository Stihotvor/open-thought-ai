# Path 2 — stateless request-response (connect per call)

Lens: tool lists are fetched once at startup and cached. Each call opens a fresh transport and session, calls the tool and closes everything.

## Files (under `server/src/ota_server/hub/`)
- `mcp_config.py`: `ServerEntry` and `load_servers(path)` (T3, E6, D13).
- `mcp_source.py`: `McpSource(ToolSource)` with a frozen spec list and `_open(entry)`, an async context manager for the transport and `ClientSession`.
- `mcp_env.py`: clean stdio env (T7).
- `mcp_startup.py`: `start_mcp_sources(settings, stack)`.
- `session.py:82`: `McpHub([DeviceSource(...), *mcp_sources])`.

## Flow
- Startup: one temporary connection per server for `list_tools`, then the allowlist (T4, E2, E3). A down server registers no specs (E1).
- Call: `asyncio.timeout(timeout)` around connect, initialize, call and close (F1). Result mapping E4, E5. One broad catch (F5).
- No reconnect logic: every call is a fresh connect (F2, F3). E7 holds because nothing is shared.

## Seam for #31
`_check_call(entry, tool, args)` before `_open` (SSRF) and a headers provider for `MCP_SECRET_*`. Hash re-approval can compare a fresh `list_tools` per call.

## Assessment
- Breaks T6 (owner decision: app-wide connections). One stdio child per call: up to 4 per turn per session. Each call adds a full handshake, and a cold `npx`/`uvx` child can take seconds (assumption). Servers with state between calls do not work.
- Effort: S to M. Smallest code, no lifecycle code, no anyio cross-task issue.
- Main risk: latency and process churn per voice turn.
- Feasibility: feasible only with an owner waiver on T6.

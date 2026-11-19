# Path 1 — supervisor task per server

Lens: one long-lived asyncio task per configured server. The task owns the SDK context managers.

## Files (under `server/src/ota_server/hub/`)
- `mcp_config.py`: reads `OTA_MCP_SERVERS_FILE`, makes `ServerConfig` objects (T3). It skips bad entries with a logged error (E6) and rejects duplicates and names that fail D13.
- `mcp_supervisor.py`: `McpServerSupervisor`, the only code that touches the SDK.
- `mcp_source.py`: `McpSource(ToolSource)` for one server, with no SDK imports.
- `mcp_registry.py`: `McpRegistry` with `start()`, `stop()`, `sources()`.
- `main.py`: the lifespan starts the registry into `application.state.mcp`.
- `session.py:82`: `McpHub([DeviceSource(...), *mcp_sources])`.

## Flow
- The supervisor task loops: it enters the transport and `ClientSession` with `async with`, calls `initialize()` and `list_tools()`, keeps the allowlisted tools (T4, E2, E3), publishes `ToolSpec`s and sets `ready`.
- It reads calls from an `asyncio.Queue` (tool, args, `Future[ToolOutcome]`). Each call runs as a child task in the supervisor task group (E7).
- `McpSource.call()` waits with `asyncio.timeout(timeout)` (F1) and maps every exception to an error outcome (F5).
- On a transport error the task leaves `async with` (this stops the stdio child), backs off (1 s doubling, cap 30 s, assumption) and reconnects (F2, F3).
- Startup waits up to 5 s per server in parallel. A down server logs a warning (E1).
- Shutdown sets a stop event and awaits the tasks with a deadline, then cancels (F4).
- Result mapping: `isError` gives a generic text (E4). Non-text parts become `[image]` or `[resource]` (E5).

## Seam for #31
`McpSource` takes an optional `Guard` protocol: `check_connect(cfg)` before connect (SSRF, secret headers) and `check_tools(cfg, tools)` after `list_tools()` (hash re-approval). Default: allow all.

## Tests
`FastMCP` fake servers for loopback HTTP, loopback SSE and a stdio script. M1–M4 and E1–E7 as in the requirements.

## Assessment
- Effort: M (about 4 new modules, 3 transports).
- Main risk: anyio cancel scopes break if a context manager is entered in one task and exited in another. This design confines enter and exit to one task. Other risks: stdio cleanup on hard cancel, and a server that becomes ready late is missing from sessions that started earlier (the hub snapshots specs at construction, `hub.py:49`).
- Feasibility: high.

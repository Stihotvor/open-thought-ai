# Decision — external MCP client (issue #28)

## Context
The hub from #27 (`server/src/ota_server/hub/`) runs tools through `ToolSource` (`hub/types.py:51`). `session.py:82` builds one `McpHub` per device session and snapshots the specs once (`hub.py:46-51`). The hub terminal cancels a call at `source.timeout + GUARD_GRACE` (0.5 s, `hub.py:87`). The `wrap()` step already truncates results to `MAX_RESULT_CHARS` (2000). This issue adds external MCP servers over Streamable HTTP, SSE and stdio as tool sources. The owner chose a JSON config file, app-wide connections and a per-server tool allowlist. Requirements: `requirements.md`. #29, #30 and #31 are out of scope, but #31 must plug in without a rewrite (B3).

## Options
- Path 1: one supervisor task per server with a call queue and futures. Feasible with changes (link caller cancel to the call task, remove the queue layer).
- Path 2: a new connection for each call. Not feasible: it breaks T6 and starts a stdio child per call.
- Path 3: ports and adapters. An SDK-free `McpSource` over an `McpConnection` port, one adapter per transport. Feasible with changes. Reviewer preference.
- Path 4: an out-of-process gateway over a JSON pipe. Feasible, highest cost, CI coverage and process-test risk.

## Decision
The owner chose Path 3 with the owner-task details from Path 1.
- D1: New package `server/src/ota_server/mcp/`: `port.py`, `config.py`, `source.py`, `adapters.py`, `registry.py`, `hooks.py`. The hub, the chain and the pipeline do not change (B2).
- D2: `port.py` holds the `McpConnection` Protocol (`list_tools`, `call_tool`, `close`) and the SDK-free dataclasses `RawTool` and `RawResult`. Only `adapters.py` imports the SDK.
- D3: Pin `mcp==1.30.0`. PyPI was checked on 2026-11-19: 1.30.0 is the latest 1.x and uses `httpx`, which the server already has. v2.3.0 needs `httpx2` and a new `Client` API. A move to v2 changes only `adapters.py`.
- D4: `SdkConnection` owns one task. That task enters the transport and `ClientSession` with `async with`, calls `initialize()`, sets `ready`, and waits on a stop event. It exits the same blocks in the same task (anyio rule). Calls from other tasks use the shared `ClientSession` directly, with no queue (E7).
- D5: `McpSource(ToolSource)` holds all policy: allowlist (T4, E2, E3), names `<server>__<tool>`, a drop of any name that fails D13 with a logged error, and result mapping (E4: generic error text, never the raw body; E5: placeholders `[image]` or `[resource]` for non-text parts). `McpSource` does not truncate. The existing `wrap()` step truncates to `MAX_RESULT_CHARS`.
- D5a: `McpSource.call` catches `Exception` only and returns an error `ToolOutcome` (F5). `asyncio.CancelledError` is always raised again, so barge-in cancel and the hub outer guard keep working. A test cancels a call from the caller side.
- D6: Timeout rule. `McpSource.call` runs the whole call, including any reconnect and retry, under one `asyncio.timeout(timeout)`. A timeout returns an error outcome and does not reconnect, because the shared session stays usable and other calls continue (E7). A test confirms that a call after a timeout succeeds.
- D7: Reconnect rule. Only a transport error (closed stream, connection-closed `McpError`, child exit) starts a reconnect. The connection owns the reconnect: `SdkConnection.reconnect()` runs under an `asyncio.Lock` and is wrapped in `asyncio.shield`, so a caller cancel or timeout cannot leave it half done. The call retries once inside the D6 deadline. If that fails, the call returns an error outcome (F2, F3). The reconnect has its own 5 s deadline (as D10), so a hung server cannot hold the lock. A generation counter is checked under the lock: a caller that saw an older generation skips the reconnect, so two concurrent failed calls cause one reconnect.
- D8: Config. A new `Settings` field `mcp_servers_file` (env `OTA_MCP_SERVERS_FILE`, default empty) points to a JSON list. No file means no servers. Entry schema:
  - `name`: str, required, must pass D13 and have no `__`, unique.
  - `transport`: `http`, `sse` or `stdio`, required.
  - `url`: str, required for `http` and `sse`. Scheme `http` or `https` only. A URL with user info or a query string is rejected (no tokens in the file, T8).
  - `command`: str and `args`: list of str, required for `stdio`.
  - `env`: dict of str to str, optional, `stdio` only. The owner must not put secret values in `env` until #31 adds `MCP_SECRET_*`.
  - `timeout`: float seconds, default 10, bounds 1 to 60.
  - `tools`: list of str, required, the allowlist. A name that fails D13 is dropped with a logged error.
  A bad JSON file, a bad entry or a duplicate name is skipped with a logged error (E6).
- D9: Stdio env is the SDK default safe set plus the entry `env` (T7). SDK 1.x `stdio_client` merges `get_default_environment()` (on POSIX: `HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, `USER`) with the entry `env`. None of these hold secrets, so T7 reads: no model keys, no `OTA_*` and no `MCP_SECRET_*` reach the child. A test checks this against the pinned SDK. Shutdown sets the stop event and waits up to 5 s for the owner task to leave its `async with` blocks. After the deadline it cancels the owner task, and the SDK `stdio_client` cleanup stops the child. The 1.x SDK does not expose the child `Process`, so the adapter does not kill a PID itself. M4 tests both the normal path and the path where the deadline passes.
- D10: `registry.py`: `McpRegistry.start(configs, stack)` connects all servers in parallel with a 5 s connect deadline each. A server that fails logs a warning and has no specs (E1). It stays off until the server restarts. There is no background retry. A source with 0 admitted tools is dropped and its connection is closed. The lifespan in `main.py` stores the sources in `app.state.mcp_sources`. `gateway.py` passes them to `DeviceSession`, and `session.py:82` builds `McpHub([DeviceSource(...), *mcp_sources])`.
- D11: #31 seam in `hooks.py`:
  - `ConnectionPolicy.before_connect(config) -> dict[str, str]` returns extra HTTP headers, or raises `ConnectionDenied` to block the server (SSRF). The adapter factory calls it before each connect and reconnect. For `stdio` it may only deny. The default allows all and returns no headers.
  - `ToolFilter.admit(server, RawTool) -> bool` runs in `McpSource` when the tool list is built (hash re-approval). The default admits all.
- D12: Tests. `McpSource` unit tests over a `FakeConnection` (no SDK) cover E2 to E6, F1, F5, D5a and D6. Per-transport tests run against `FastMCP` fake servers: uvicorn on 127.0.0.1 for http and sse, and a stdio child script in `server/tests/`. They cover M1 (list and call, 3 of 3), E7 (two concurrent calls on one shared session, http), E1 (one server down at startup, the other sources still listed), F3 (stdio child exit, then 1 reconnect and 1 successful call), M2 (error within `timeout + 0.5 s`), M3 (http fake server restart, 1 reconnect, then 1 successful call) and M4 (0 children after shutdown). M5: `server/scripts/gate.sh` passes with coverage >= 90 % (T10).

## Consequences
- Known limit: the hub snapshots specs per session. A server that is down at startup stays off until the server restarts. The tool list does not refresh after the first connect.
- A timed-out request can still run on the external server. The SDK drops a late response.
- The SDK pin is 1.x. A later move to 2.x is a change in one file.
- New dependency `mcp==1.30.0`, plus its transitive dependencies in `uv.lock`.

# Path 3 — hexagonal ports and adapters

Lens: `McpSource` holds all policy and never imports the SDK. It talks to an `McpConnection` port. One adapter per transport wraps the SDK.

## Files (new package `server/src/ota_server/mcp/`)
- `port.py`: `McpConnection` Protocol (`list_tools`, `call_tool`, `close`) and SDK-free `RawTool`, `RawResult`.
- `config.py`: `ServerConfig`, `load_servers(path)` (T3, E6, D13).
- `source.py`: `McpSource(ToolSource)`: allowlist (T4, E2, E3), naming, result mapping (E4, E5, truncation), timeout (F1), one reconnect on a connection error (F2, F3), broad catch (F5).
- `adapters.py`: `SdkConnection` and `http_factory`, `sse_factory`, `stdio_factory`. The stdio factory builds the clean env (T7).
- `registry.py`: `McpRegistry.start(configs, stack)`, `sources()`.
- `hooks.py`: #31 seam Protocols.
- `session.py:82`, `main.py` lifespan, `gateway.py`: pass `app.state.mcp_sources` into the hub.

## Flow
- `SdkConnection` owns one background task that enters and exits the anyio context managers in the same task and waits on a stop event. `list_tools` and `call_tool` run from other tasks on the shared `ClientSession`, which accepts concurrent requests (E7).
- A short `asyncio.Lock` guards reconnect only.
- Shutdown: the lifespan stack calls `close()`, which stops the task and the stdio child (F4).

## Seam for #31
`ToolFilter.admit(server, RawTool)` in `start()` (hash check). `ConnectionPolicy.before_connect(config)` returns headers (SSRF, `MCP_SECRET_*`). The SDK version (v1.x or v2.3.0) is isolated in `adapters.py`.

## Tests
Unit tests of `McpSource` over a `FakeConnection` (no SDK). Per-transport tests against FastMCP fakes on 127.0.0.1 and a stdio child (M1, M3, M4). Config tests (E6).

## Assessment
- Effort: M (six small modules, three fixtures).
- Main risk: a cancelled `call_tool` after a timeout can leave the session in a bad state, so a connection error triggers a full reconnect. The hub snapshots specs per session, so a server that is down at startup stays unlisted until a new session.
- Feasibility: high. The hub, chain and pipeline do not change.

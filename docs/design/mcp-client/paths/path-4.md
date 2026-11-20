# Path 4 — out-of-process gateway

Lens: a child process `python -m ota_server.mcp_gateway` owns every MCP connection and stdio child on its own event loop. The main server talks to it with JSON lines over stdin and stdout.

## Files (`server/src/ota_server/mcp/`)
- `config.py`: shared config loader (T3, T5, E6).
- `core.py`: `GatewayCore`: connect, allowlist (T4, E2, E3), call, timeout (F1), one reconnect per call (F2, F3), content to text (E4, E5).
- `gateway_main.py`: the pipe loop. Each request runs as a task. Stdin EOF closes the core and exits.
- `client.py`: `GatewayClient` (spawn and supervise the child) and `GatewaySource(ToolSource)`.
- `session.py:82`, `gateway.py:81`, `main.py` lifespan: wire the sources.

## Flow
- Lifespan spawns the gateway with a clean env. The gateway sends a `hello` line with the allowlisted specs (E1 for a down server).
- `GatewaySource.call` sends `{id, server, tool, args}` and waits for its id under `timeout + 0.25 s`. Ids separate concurrent calls (E7). Pipe errors become error outcomes (F5).
- Shutdown closes stdin, waits about 5 s, then kills the process group (F4). `PR_SET_PDEATHSIG` covers a hard kill (assumption).

## Seam for #31
SSRF, `MCP_SECRET_*` and hash checks go in `GatewayCore`. Secrets stay in the gateway env only.

## Assessment
- Effort: M, about 150 more lines than an in-process client (pipe protocol, supervision).
- Risks: subprocess coverage and flaky process tests in CI (T10, M5). Extra failure point against T1 simplicity. The container limits (256 MB, `pids_limit: 128`, `read_only`) are shared. A gateway crash respawns but does not rebuild hub specs.
- Feasibility: high, with extra cost.

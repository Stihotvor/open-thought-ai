# Path 3 — network-layer guard in the HTTP client

- New `mcp/netguard.py`: `HostPolicy` (allowlist, blocked ranges, single-label rule, IPv4-mapped unwrap), `Resolver` protocol, `GuardedTransport` (httpx transport) and `GuardedBackend` (httpcore network backend that resolves, checks all IPs and connects to the vetted IP), `NetBlocked(httpx.TransportError)`, `guarded_client_factory`.
- The guard runs on every request, reconnects included. It closes DNS rebinding because check and connect share one resolution. It raises on any 3xx (T8).
- Verified in `.venv` mcp 1.30.0: `sse_client` accepts `httpx_client_factory(headers=, auth=, timeout=)`; `streamable_http_client` takes `http_client=`. The SDK `stream_within_origin` follows same-origin 307/308 itself, so `follow_redirects=False` is not enough: the guard must raise on 3xx.
- New `mcp/secrets.py` `resolve_headers` (T9). `hooks.py`: real `ConnectionPolicy` (T5, secret presence) and `HashToolFilter` (T4, T3). Config rules as path 1. `main.py` injects (T11).
- Effort M. Risks: httpcore backend is semi-public API; SDK bumps may add redirect paths (keep a same-origin 307 test as tripwire); stdio covered only by config rules.

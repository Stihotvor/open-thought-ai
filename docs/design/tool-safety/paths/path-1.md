# Path 1 — hexagonal plugins on the existing seams

- New `mcp/policy.py`: `SafeConnectionPolicy(allowed_hosts, resolver, env)` implements `ConnectionPolicy.before_connect`: T5 scheme rule, T6 resolve and check all IPs, T9 secret header lookup. Raises `ConnectionDenied`.
- New `mcp/ssrf.py`: `check_host(host, allowlist, resolver)`, stdlib `ipaddress`, injectable resolver (T13).
- New `mcp/toolfilter.py`: `HashToolFilter(pins)` implements `ToolFilter.admit`: T4 caps, then T3 hash. `tool_hash(tool)` over canonical JSON.
- `config.py`: `tools` map, `headers` field, `device` reserved, cross-entry `MCP_SECRET_*` check, stdio env rules (T10, E6).
- `adapters.py`: redirect-free httpx client (T8). `hub.py`: first full name wins, log error (T2).
- `main.py`: lifespan builds and injects policy and filter (T11).
- F4: try/except around policy and filter calls, treated as deny.
- Effort M. Risk: DNS rebinding (check and connect resolve separately), accepted. Finding from path 3: `follow_redirects=False` alone does not stop SDK 1.30.0 same-origin redirects.

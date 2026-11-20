# Path 2 — load-time validation pipeline

- `config.py`: `load_pipeline(path, settings) -> list[ValidatedServer]`. Ordered pure stages: parse, name rules, url and scheme (T5 incl. allowlist), headers (T9, cross-entry `seen_secrets`), stdio env (T10, E6), tools map (T3 list form rejected). Frozen `ValidatedServer` holds pins and secret env var names, never values.
- Runtime takes only `ValidatedServer`. `mcp/ssrf.py` `check_host` runs in `SafetyPolicy.before_connect` on each connect and reconnect (T6, F2). `HashFilter` does T4 then T3.
- `adapters.py`: no-redirect client (T8). `main.py` injects (T11). Hub keeps first name (T2).
- Effort M. Cost: the `McpServerConfig` shape changes in registry, source, adapters and existing tests. Risk: host logic in two places (load and connect); share one `host_allowed` helper. Rebinding gap as path 1.

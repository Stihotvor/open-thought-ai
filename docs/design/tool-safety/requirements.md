# Requirements — tool safety (issue #31)

Fourth issue of milestone M4 (MCP hub). #27 built the hub and the device source. #28 (PR #64) built the external MCP client with two empty seams in `server/src/ota_server/mcp/hooks.py`: `ConnectionPolicy` and `ToolFilter`. This issue fills these seams and closes the four security items from the #28 review (comment on #31). Rules: `docs/security.md:34-41,49`. #29 (n8n example), #30 (`confirm` flow) and the #28 performance follow-up are out of scope. The cap of 4 tool calls per turn exists already (`hub/types.py:37`, `hub/steps.py:31`) and does not change.

## Business Goals
- B1: The owner can connect an external MCP server, and a changed or hostile server cannot change what the LLM sees without the owner's approval.
- B2: A servers file cannot make the server call internal hosts (SSRF) or leak secrets, unless the owner allows the host on purpose.
- B3: #29 can connect the n8n host with config only: one allowlisted host and one `MCP_SECRET_*` header.

## Technical Constraints
- T1: Python 3.12 FastAPI server in `server/`, ruff, strict mypy, coverage >= 90 %. Budget 0 €. No new dependency.
- T2: Names. A tool name is `<source>__<tool>`. The source name `device` is reserved for `DeviceSource`. A servers file entry named `device` is rejected. Two specs with one full name in one hub are an error: the hub keeps the first and logs an error for the second.
- T3: Hash approval (owner decision: pin in the servers file). `tools` changes from a list to a map `{tool name: "sha256:<hex>"}`. The hash is SHA-256 over the canonical JSON (sorted keys, no spaces) of the tool name, description and input schema. A tool with a missing or different hash is not admitted, and the server logs the tool name and its current hash at WARNING. The old list form is rejected with a logged error.
- T4: Size caps, checked before the hash. A tool description over 1 000 characters or an input schema over 4 000 characters of canonical JSON is not admitted, with a logged error.
- T5: URL rules for `http` and `sse`. The scheme must be `https://`. `http://` is allowed only when the host is loopback or is on the allowlist.
- T6: SSRF check on connect and on each reconnect. The server resolves the host and checks every resolved IP. It blocks loopback, link-local (includes `169.254.169.254`), private ranges (`10/8`, `172.16/12`, `192.168/16`, `fc00::/7`), unspecified and multicast addresses, and single-label host names (compose service names). An exact host on the allowlist skips the IP check (owner decision: block LAN unless allowlisted).
- T7: Allowlist (owner decision): env `OTA_MCP_ALLOWED_HOSTS`, a comma-separated list of host names or IPs, default empty. It is a `Settings` field.
- T8: No redirects. The HTTP client for `http` and `sse` does not follow redirects. A redirect response is a connect or call error.
- T9: Secret headers. An `http` or `sse` entry may have `headers: {header name: "MCP_SECRET_<NAME>"}`. The value is the name of an env variable, never a literal. A value that does not start with `MCP_SECRET_` rejects the entry. One `MCP_SECRET_*` name may appear in one entry only (bound to one host). A missing variable at connect time denies the server with a logged error that names the variable but not a value.
- T10: Stdio env (owner decision: reject). A stdio entry is rejected when an `env` key contains `KEY`, `TOKEN`, `SECRET`, `PASSWORD` or `AUTH` (case-insensitive), or a value starts with `MCP_SECRET_`. The child never gets `MCP_SECRET_*` values (`docs/security.md:49`).
- T11: Injection. The `main.py` lifespan builds the real `ConnectionPolicy` and `ToolFilter` from `Settings` and passes them to `McpRegistry.start`. Tests can pass other objects.
- T12: Logs never contain secret header values (`docs/security.md:66`).
- T13: Tests use local fake MCP servers on 127.0.0.1 and fake DNS resolvers. No external service.

## Edge Cases
- E1: A host resolves to two IPs, one public and one private: the server is denied.
- E2: The allowlist holds `n8n` and the URL host is `N8N`: host match is case-insensitive.
- E3: An entry lists a tool name that the server does not have: logged warning, as in #28.
- E4: A server changes a tool description after a restart: the next startup does not admit the tool and logs the new hash.
- E5: An IPv4-mapped IPv6 address (`::ffff:10.0.0.1`) is checked as its IPv4 address.
- E6: An entry has `headers` with `transport: stdio`: the entry is rejected.
- E7: DNS resolution fails: the server is denied with a logged warning. The other servers start.

## Failure Modes
- F1: A denied server (SSRF, missing secret) does not stop startup. The device tools and the other servers keep working (as #28 E1).
- F2: A reconnect to a host that now resolves to a blocked IP is denied. The call returns an error outcome and the session survives.
- F3: A tool that is not admitted is hidden. A call to it is an unknown tool.
- F4: A policy or filter exception is logged and treated as a deny. `McpHub.run` never raises.

## Success Metrics
- M1: Unit tests cover each blocked range in T6 (at least 7 cases) and the allowlist bypass: all pass.
- M2: Hash tests: 1 match admitted, 1 missing hash and 1 changed description not admitted, 1 description of 1 001 chars not admitted: 4 of 4 pass.
- M3: A fake http server that returns a 307 redirect makes the connect fail: 1 of 1 pass.
- M4: A fake http server receives the secret header value from a test env variable, and the log output of the test has 0 occurrences of that value.
- M5: Config tests reject: a `device` name, a literal header value, a shared `MCP_SECRET_*` name, a stdio `env` key `API_KEY`, the old `tools` list form: 5 of 5 pass.
- M6: `server/scripts/gate.sh` passes: ruff 0 findings, mypy 0 errors, all tests pass, coverage >= 90 %.

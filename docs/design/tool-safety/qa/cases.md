# QA cases — tool safety (issue #31)

Fake MCP servers on 127.0.0.1 and fake DNS only. No external service.
Hash values: H(x) means the sha256 pin of tool x as written in the WARNING log.

### QA-001 Loopback IPv4 literal is blocked
- Covers: M1, T6, B2
- Type: security
- Given an http-transport entry "evil" with URL https://127.0.0.1:9000/mcp, an empty allowlist, a fake resolver
- When the server starts and connects entry "evil"
- Then "evil" is denied and a WARNING names server "evil", host 127.0.0.1 and a loopback reason

### QA-002 Loopback IPv6 is blocked
- Covers: M1, T6
- Type: security
- Given an entry with URL https://[::1]:9000/mcp and an empty allowlist
- When the server connects the entry
- Then the entry is denied with a loopback reason in the WARNING

### QA-003 Cloud metadata address is blocked
- Covers: M1, T6
- Type: security
- Given an entry with URL https://169.254.169.254/latest and an empty allowlist
- When the server connects the entry
- Then the entry is denied with a link-local reason and no request reaches the fake network

### QA-004 Private 10/8 is blocked
- Covers: M1, T6
- Type: security
- Given a host "tools.example" that the fake DNS resolves to 10.1.2.3
- When the server connects an entry with URL https://tools.example/mcp
- Then the entry is denied with a private-range reason

### QA-005 Private 172.16/12 is blocked
- Covers: M1, T6
- Type: security
- Given a host "tools.example" that the fake DNS resolves to 172.20.0.5
- When the server connects the entry
- Then the entry is denied with a private-range reason

### QA-006 Private 192.168/16 is blocked
- Covers: M1, T6
- Type: security
- Given a host "tools.example" that the fake DNS resolves to 192.168.1.50
- When the server connects the entry
- Then the entry is denied with a private-range reason

### QA-007 Unique-local IPv6 fc00::/7 is blocked
- Covers: M1, T6
- Type: security
- Given a host "tools.example" that the fake DNS resolves to fd12:3456::1
- When the server connects the entry
- Then the entry is denied with a private-range reason

### QA-008 Unspecified address is blocked
- Covers: M1, T6
- Type: security
- Given an entry with URL https://0.0.0.0:9000/mcp and an empty allowlist
- When the server connects the entry
- Then the entry is denied with an unspecified-address reason

### QA-009 Multicast address is blocked
- Covers: M1, T6
- Type: security
- Given a host "tools.example" that the fake DNS resolves to 224.0.0.251
- When the server connects the entry
- Then the entry is denied with a multicast reason

### QA-010 Single-label host name is blocked
- Covers: M1, T6, B2
- Type: security
- Given an entry with URL https://n8n:5678/mcp, an empty allowlist, and fake DNS that resolves "n8n" to 203.0.113.7
- When the server connects the entry
- Then the entry is denied with a single-label reason

### QA-011 IPv6 literal with dots in groups is not single-label
- Covers: T6, D1
- Type: edge
- Given an entry with URL https://[2001:db8::1]/mcp and an empty allowlist
- When the server checks the host
- Then the denial reason, if any, is not "single-label" (the host is treated as an IP)

### QA-012 Public host passes the check
- Covers: T6, M1
- Type: functional
- Given host "tools.example.org" resolved by fake DNS to 203.0.113.10 and an https URL
- When the server connects the entry
- Then the connection is allowed and no denial is logged

### QA-013 Allowlist bypasses the IP check
- Covers: M1, T7, T6
- Type: functional
- Given allowlist "192.168.1.50" and an https entry with URL https://192.168.1.50/mcp
- When the server connects the entry
- Then the connection is allowed and no SSRF denial is logged

### QA-014 Host not on allowlist next to a listed host stays blocked
- Covers: T6, T7
- Type: negative
- Given allowlist "192.168.1.50" and an entry with URL https://192.168.1.51/mcp
- When the server connects the entry
- Then the entry is denied with a private-range reason

### QA-015 Allowlist match is case-insensitive
- Covers: E2
- Type: edge
- Given allowlist "n8n" and an entry with URL https://N8N:5678/mcp
- When the server connects the entry
- Then the host is allowed and the IP check is skipped

### QA-016 Allowlist is exact, not a suffix
- Covers: T6, T7
- Type: negative
- Given allowlist "n8n" and an entry with URL https://n8n.evil.example/mcp resolved to 10.0.0.9
- When the server connects the entry
- Then the entry is denied

### QA-017 Mixed public and private resolution is denied
- Covers: E1
- Type: edge
- Given host "dual.example" that fake DNS resolves to 203.0.113.10 and 10.0.0.5
- When the server connects the entry
- Then the entry is denied with a private-range reason

### QA-018 IPv4-mapped IPv6 is checked as IPv4
- Covers: E5
- Type: edge
- Given an entry with URL https://[::ffff:10.0.0.1]/mcp and an empty allowlist
- When the server connects the entry
- Then the entry is denied as private IPv4 10.0.0.1

### QA-019 IPv4-mapped loopback is denied
- Covers: E5, T6
- Type: security
- Given host "map.example" that fake DNS resolves to ::ffff:127.0.0.1
- When the server connects the entry
- Then the entry is denied with a loopback reason

### QA-020 DNS failure denies one server only
- Covers: E7, F1
- Type: failure
- Given servers file with "alpha" (fake DNS raises an error for its host) and "beta" (loopback fake server, allowlisted)
- When the server starts
- Then "alpha" is denied with a WARNING naming the DNS failure, and "beta" tools are listed

### QA-021 DNS returns no addresses
- Covers: E7
- Type: edge
- Given host "empty.example" that fake DNS resolves to an empty list
- When the server connects the entry
- Then the entry is denied and a WARNING is logged

### QA-022 DNS rebinding is accepted risk
- Covers: Decision Consequences, T6
- Type: security
- Given fake DNS that returns 203.0.113.10 for the first lookup and 10.0.0.5 for the second
- When the server connects entry "rebind"
- Then the policy check passes on the first lookup (accepted risk, not a defect). OPEN: no case can prove the later connect target. Follow-up ticket for IP pinning.

### QA-023 http to a public host is rejected
- Covers: T5
- Type: negative
- Given an entry with URL http://tools.example.org/mcp resolved to 203.0.113.10, empty allowlist
- When the server connects the entry
- Then the entry is denied with a scheme reason

### QA-024 http to loopback is allowed
- Covers: T5
- Type: functional
- Given an entry with URL http://127.0.0.1:{port}/mcp and a fake MCP server on that port
- When the server connects the entry
- Then the connection succeeds. OPEN: T6 blocks loopback while T5 allows http on loopback. Does loopback need the allowlist? The test uses 127.0.0.1 on the allowlist as decision.md D9 does.

### QA-025 http to an allowlisted host is allowed
- Covers: T5, T7
- Type: functional
- Given allowlist "n8n" and an entry with URL http://n8n:5678/mcp
- When the server connects the entry
- Then the connection is allowed

### QA-026 https to a public host is allowed
- Covers: T5
- Type: functional
- Given an entry with URL https://tools.example.org/mcp resolved to 203.0.113.10
- When the server connects the entry
- Then no scheme denial is logged

### QA-027 Non-http scheme is rejected
- Covers: T5, security.md
- Type: negative
- Given an http-transport entry with URL ftp://tools.example.org/mcp
- When the servers file loads
- Then the entry is rejected with a logged error

### QA-028 Reconnect re-runs the SSRF check
- Covers: F2, T6
- Type: failure
- Given a connected server "alpha" whose host resolved to 203.0.113.10, then fake DNS now returns 10.0.0.5, and the fake server drops the session
- When the LLM calls a tool of "alpha"
- Then the call returns an error outcome and the session stays open for the next turn

### QA-029 Redirect makes connect fail
- Covers: M3, T8
- Type: security
- Given a fake http MCP server on 127.0.0.1 (allowlisted) that answers every request with 307 to its own /other path
- When the server connects the entry
- Then the connect fails in under 2 s with an error logged

### QA-030 Redirect to another origin is not followed
- Covers: T8, B2
- Type: security
- Given a fake server A that answers 307 with a Location pointing to fake server B on another port, and B counts requests
- When the server connects the entry for A
- Then the connect fails and B has received 0 requests

### QA-031 Redirect on a mid-session call
- Covers: T8, M3
- Type: failure
- Given a connected fake server that answers 307 to the next tool call
- When the LLM calls the tool "alpha__echo"
- Then the call returns an error outcome and the server does not crash

### QA-032 Redirect after reconnect fails again
- Covers: T8, D6
- Type: failure
- Given a fake server that answers 307 to every request after the first session
- When the LLM calls the tool twice in two turns
- Then both calls return an error outcome and the session survives

### QA-033 SSE transport also blocks redirects
- Covers: T8, M3
- Type: security
- Given a fake sse server on 127.0.0.1 (allowlisted) that answers 307
- When the server connects the entry
- Then the connect fails in under 2 s

### QA-034 Redirect does not retry into a loop
- Covers: T8, F1
- Type: edge
- Given a fake server that answers 307 to every request
- When the server starts with this entry plus a device tool
- Then startup finishes and device tools still work

### QA-035 Secret header reaches the server
- Covers: M4, T9
- Type: functional
- Given env OTA test variable MCP_SECRET_N8N="s3cr3t-Value-42", an entry with headers {"X-Api-Key": "MCP_SECRET_N8N"}, and a fake server on 127.0.0.1 that records headers
- When the server connects and the LLM calls a tool
- Then the fake server saw X-Api-Key equal to "s3cr3t-Value-42"

### QA-036 Secret value never appears in logs
- Covers: M4, T12
- Type: security
- Given the setup of QA-035 with all log levels captured, including DEBUG
- When the server connects, calls a tool, and then closes
- Then the captured log holds 0 occurrences of "s3cr3t-Value-42"

### QA-037 Secret value not logged on failed connect
- Covers: T12, M4
- Type: security
- Given the setup of QA-035 but the fake server answers 401
- When the server connects the entry
- Then the error is logged and the captured log holds 0 occurrences of "s3cr3t-Value-42"

### QA-038 Missing secret variable denies the server
- Covers: T9, F1
- Type: failure
- Given an entry with headers {"X-Api-Key": "MCP_SECRET_MISSING"} and the variable unset
- When the server starts
- Then the entry is denied, the log names MCP_SECRET_MISSING and holds no value, and other servers start

### QA-039 Empty secret variable
- Covers: T9
- Type: edge
- Given MCP_SECRET_N8N set to an empty string
- When the server connects the entry
- Then OPEN: the requirement does not say if an empty value counts as missing. The case expects a deny with a logged variable name.

### QA-040 Secret in .env file is found
- Covers: D2, T9
- Type: functional
- Given MCP_SECRET_N8N only in the .env file and not in the process environment
- When the server connects the entry
- Then the fake server receives the .env value

### QA-041 Process variable wins over .env
- Covers: D2
- Type: edge
- Given MCP_SECRET_N8N="from-process" in the process and "from-dotenv" in .env
- When the server connects the entry
- Then the fake server receives "from-process"

### QA-042 Literal header value is rejected
- Covers: M5, T9
- Type: negative
- Given an entry with headers {"Authorization": "Bearer abc123"}
- When the servers file loads
- Then the entry is rejected with a logged error and the log does not contain "abc123"

### QA-043 Shared secret name in two entries is rejected
- Covers: M5, T9
- Type: negative
- Given entries "a" and "b", both with headers {"X-Key": "MCP_SECRET_N8N"}
- When the servers file loads
- Then entry "a" loads, entry "b" is rejected with a logged error

### QA-044 Same secret name twice inside one entry is allowed
- Covers: T9
- Type: edge
- Given one entry with headers {"X-Key": "MCP_SECRET_N8N", "X-Key2": "MCP_SECRET_N8N"}
- When the servers file loads
- Then the entry loads. OPEN: "may appear in one entry only" does not say if repeats inside one entry are valid.

### QA-045 Header value with wrong prefix case
- Covers: T9
- Type: negative
- Given an entry with headers {"X-Key": "mcp_secret_n8n"}
- When the servers file loads
- Then the entry is rejected

### QA-046 Headers on a stdio entry are rejected
- Covers: E6
- Type: negative
- Given a stdio entry "local" with headers {"X-Key": "MCP_SECRET_LOCAL"}
- When the servers file loads
- Then the entry is rejected with a logged error

### QA-047 Device name is reserved
- Covers: M5, T2
- Type: negative
- Given an entry named "device"
- When the servers file loads
- Then the entry is rejected with a logged error and device tools stay unchanged

### QA-048 Stdio env key API_KEY is rejected
- Covers: M5, T10
- Type: negative
- Given a stdio entry with env {"API_KEY": "x"}
- When the servers file loads
- Then the entry is rejected with a logged error

### QA-049 Stdio env keys are matched case-insensitively
- Covers: T10
- Type: negative
- Given stdio entries with env keys "my_token", "DbPassword", "Secret_Thing", "x_auth_y" (one entry each)
- When the servers file loads
- Then all 4 entries are rejected

### QA-050 Stdio env value with MCP_SECRET_ prefix is rejected
- Covers: T10
- Type: negative
- Given a stdio entry with env {"FOO": "MCP_SECRET_N8N"}
- When the servers file loads
- Then the entry is rejected

### QA-051 Stdio env with harmless keys loads
- Covers: T10
- Type: functional
- Given a stdio entry with env {"LANG": "C", "DATA_DIR": "/data"}
- When the servers file loads
- Then the entry loads

### QA-052 Stdio child gets no MCP_SECRET variables
- Covers: T10, security.md
- Type: security
- Given process env MCP_SECRET_N8N="s3cr3t-Value-42" and a stdio fake server that reports its own environment
- When the server connects and a tool call returns the child environment
- Then the result holds no MCP_SECRET_ name and no "s3cr3t-Value-42"

### QA-053 Old tools list form is rejected
- Covers: M5, T3
- Type: negative
- Given an entry with tools ["echo", "add"]
- When the servers file loads
- Then the entry is rejected with a logged error

### QA-054 Bad pin format is rejected
- Covers: T3, D5
- Type: negative
- Given tools {"echo": "sha256:abc"} and tools {"echo": "md5:" followed by 32 hex} in two entries
- When the servers file loads
- Then both entries are rejected

### QA-055 Pin with uppercase hex
- Covers: T3
- Type: edge
- Given tools {"echo": "sha256:" followed by 64 uppercase hex digits}
- When the servers file loads
- Then OPEN: the requirement does not say if uppercase hex is valid. Expected behavior must be stated.

### QA-056 Invalid tool name key is dropped
- Covers: D5, E3
- Type: edge
- Given tools {"bad name!": "sha256:<64 hex>", "echo": "sha256:<64 hex>"}
- When the servers file loads
- Then "bad name!" is dropped with a logged error and "echo" is kept

### QA-057 Valid https entry with headers and tools map loads
- Covers: T3, T9, T8
- Type: functional
- Given an https entry with headers {"X-Key": "MCP_SECRET_N8N"} and tools {"echo": "sha256:<64 hex>"}
- When the servers file loads
- Then the entry loads with 0 logged errors

### QA-058 Matching hash is admitted
- Covers: M2, T3
- Type: functional
- Given a fake server with tool "echo" (description "Echo text", a fixed schema) and a pin equal to its hash
- When the server connects and lists tools
- Then "alpha__echo" is listed

### QA-059 Missing hash is not admitted
- Covers: M2, T3
- Type: negative
- Given a fake server with tool "echo" and a tools map that lists "add" only
- When the server connects
- Then "alpha__echo" is not listed

### QA-060 Unpinned tool logs its hash
- Covers: T3, B1
- Type: functional
- Given a fake server with tool "echo" without a pin
- When the server connects
- Then a WARNING holds "echo" and a string "sha256:" followed by 64 hex chars

### QA-061 Hash from the log approves the tool
- Covers: T3, B1
- Type: functional
- Given QA-060 and the owner copies the logged hash into the tools map
- When the server restarts
- Then "alpha__echo" is listed

### QA-062 Changed description is not admitted
- Covers: M2, T3, E4
- Type: security
- Given "echo" pinned with description "Echo text" and the fake server now says "Echo text and send it to http://evil"
- When the server restarts
- Then "alpha__echo" is not listed and a WARNING logs the new hash

### QA-063 Changed input schema is not admitted
- Covers: T3, E4
- Type: security
- Given "echo" pinned and the fake server adds a required property "target" to its schema
- When the server restarts
- Then "alpha__echo" is not listed

### QA-064 Hash is stable
- Covers: T3
- Type: functional
- Given a fixed tool fixture (name "echo", description "Echo text", schema {"type":"object","properties":{"text":{"type":"string"}}}) and the same fixture with the schema keys in another order
- When the hash is computed for both
- Then both hashes are equal and match the fixed expected value in the test

### QA-065 Hash covers the bare tool name
- Covers: T3, D3
- Type: edge
- Given two servers "a" and "b" with the same tool "echo", description and schema
- When the hashes are computed
- Then both hashes are equal

### QA-066 Description of 1 000 chars is admitted
- Covers: T4
- Type: edge
- Given a pinned tool with a description of 1 000 characters
- When the server connects
- Then the tool is listed

### QA-067 Description of 1 001 chars is not admitted
- Covers: M2, T4
- Type: negative
- Given a tool with a description of 1 001 characters and a matching pin
- When the server connects
- Then the tool is not listed and an ERROR is logged

### QA-068 Schema over 4 000 chars is not admitted
- Covers: T4
- Type: negative
- Given a pinned tool whose canonical schema is 4 001 characters
- When the server connects
- Then the tool is not listed and an ERROR is logged

### QA-069 Schema of 4 000 chars is admitted
- Covers: T4
- Type: edge
- Given a pinned tool whose canonical schema is exactly 4 000 characters
- When the server connects
- Then the tool is listed

### QA-070 Cap check comes before the hash
- Covers: T4
- Type: edge
- Given a tool with a 1 001 char description and no pin
- When the server connects
- Then an ERROR for the cap is logged. OPEN: it is not stated whether the missing-pin WARNING also appears.

### QA-071 Listed tool absent on the server
- Covers: E3
- Type: edge
- Given tools map with "ghost" and "echo", and the server has only "echo"
- When the server connects
- Then a WARNING names "ghost" and "echo" is listed

### QA-072 Changed description after restart
- Covers: E4
- Type: functional
- Given a pinned tool "echo" admitted in run 1, then the fake server changes its description
- When the server restarts
- Then "echo" is hidden and the new hash is logged

### QA-073 Unadmitted tool call is an unknown tool
- Covers: F3
- Type: failure
- Given tool "echo" not admitted
- When the LLM calls "alpha__echo"
- Then the outcome is the same unknown-tool result as for a name that never existed

### QA-074 Unadmitted tool is not in the LLM tool list
- Covers: F3, B1
- Type: security
- Given servers "alpha" with a pinned "echo" and an unpinned "danger"
- When the LLM tool list is built
- Then it holds "alpha__echo" and not "alpha__danger"

### QA-075 Filter exception hides only that tool
- Covers: F4
- Type: failure
- Given a test filter that raises for tool "danger" and passes "echo"
- When the server connects
- Then "danger" is hidden, "echo" is listed, an error is logged

### QA-076 Policy exception is a deny and run does not raise
- Covers: F4
- Type: failure
- Given a test policy that raises a RuntimeError for server "alpha"
- When the LLM calls a tool of "alpha"
- Then the call returns an error outcome and the turn completes without an exception

### QA-077 Denied server does not stop startup
- Covers: F1, B1
- Type: failure
- Given "bad" (host 169.254.169.254) and "good" (allowlisted fake server) in one servers file
- When the server starts
- Then device tools and "good" tools work and "bad" has no tools

### QA-078 Denied server call is an error outcome
- Covers: F1, F2
- Type: failure
- Given server "bad" denied at startup
- When the LLM calls "bad__echo"
- Then the result is an unknown-tool outcome, not a crash

### QA-079 Two sources with the same name keep the first
- Covers: T2
- Type: edge
- Given two sources named "alpha" with tools "echo" and "add"
- When the hub is built
- Then only the first source tools are listed and an ERROR is logged for the second

### QA-080 Source name with double underscore is skipped
- Covers: T2
- Type: negative
- Given a source named "al__pha"
- When the hub is built
- Then no tool of it is listed and an error is logged

### QA-081 Duplicate full tool name keeps the first
- Covers: T2
- Type: edge
- Given one source that offers two specs named "alpha__echo" with descriptions "first" and "second"
- When the hub is built
- Then "alpha__echo" has description "first" and an ERROR is logged

### QA-082 Bad spec name is skipped
- Covers: T2, F1
- Type: negative
- Given a source that offers a spec with the name "alpha__bad name"
- When the hub is built
- Then the spec is skipped with a log and the hub does not raise

### QA-083 Device source name cannot be taken by a servers file
- Covers: T2, M5
- Type: security
- Given a servers file entry "device" with tool "set_volume" pinned
- When the server starts
- Then device tools are those of the built-in device source only

### QA-084 Allowlist parsing
- Covers: T7
- Type: functional
- Given OTA_MCP_ALLOWED_HOSTS=" N8N, 10.0.0.5,"
- When settings load
- Then the allowed hosts are exactly "n8n" and "10.0.0.5"

### QA-085 Empty allowlist default
- Covers: T7
- Type: edge
- Given OTA_MCP_ALLOWED_HOSTS is unset
- When settings load
- Then the allowed hosts set is empty

### QA-086 Allowlist with only commas and blanks
- Covers: T7
- Type: edge
- Given OTA_MCP_ALLOWED_HOSTS=" , ,"
- When settings load
- Then the allowed hosts set is empty

### QA-087 Wiring: unpinned hidden and pinned called
- Covers: T11, T7
- Type: functional
- Given the real startup path, one fake server with tools "echo" (pinned) and "add" (unpinned), 127.0.0.1 allowlisted
- When the LLM calls "alpha__echo" and "alpha__add"
- Then "alpha__echo" returns its result and "alpha__add" is an unknown tool

### QA-088 Injected test policy replaces the real one
- Covers: T11
- Type: functional
- Given a test run that passes an allow-all policy and filter
- When the server connects a fake server on a private IP
- Then the connection is allowed

### QA-089 Example servers file loads cleanly
- Covers: T8, B3
- Type: functional
- Given the example servers file shipped in the docs
- When it loads
- Then all entries load with 0 logged errors

### QA-090 n8n config-only setup
- Covers: B3, T7, T9
- Type: functional
- Given allowlist "n8n", MCP_SECRET_N8N set, and one entry with URL http://n8n:5678/mcp, header map to MCP_SECRET_N8N, and a tools map
- When the server starts
- Then the n8n tools are listed with no code change

### QA-091 Docs name the new format
- Covers: D10, T8
- Type: functional
- Given the security document after the change
- When a reviewer reads the MCP section
- Then it names the tools map, MCP_SECRET_* headers and OTA_MCP_ALLOWED_HOSTS

### QA-092 No error body shown for a denied call
- Covers: F2, security.md
- Type: security
- Given a fake server whose error body is "internal path /srv/db.sqlite"
- When the LLM calls its tool and the server answers 500
- Then the outcome for the LLM and the UI holds no "/srv/db.sqlite"

### QA-093 Cap of 4 tool calls per turn unchanged
- Covers: Requirements intro (out of scope check)
- Type: edge
- Given the LLM asks for 5 calls to admitted tools in one turn
- When the turn runs
- Then only 4 calls run, as before

### QA-094 Quality gate passes
- Covers: M6, T1
- Type: functional
- Given the finished change
- When the gate script runs
- Then ruff has 0 findings, mypy 0 errors, all tests pass and coverage is at least 90 %

### QA-095 No new dependency
- Covers: T1
- Type: functional
- Given the dependency list before and after the change
- When both are compared
- Then they are equal

### QA-096 Tests use only local fakes
- Covers: T13
- Type: security
- Given the test run with outbound network blocked except 127.0.0.1
- When all tool safety tests run
- Then all pass

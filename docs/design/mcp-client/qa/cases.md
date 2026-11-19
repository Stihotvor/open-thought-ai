# QA cases: mcp-client (issue #28)

### QA-001 List and call over Streamable HTTP
- Covers: M1, B1
- Type: functional
- Given a fake HTTP MCP server "notes" on 127.0.0.1 with tools "add" and "secret_op", and a config with transport http, timeout 10 and allowlist ["add"]
- When the LLM calls "notes__add" with a=2 and b=3
- Then the LLM receives the text "5" and the tool list shows "notes__add" only

### QA-002 List and call over SSE
- Covers: M1, B1
- Type: functional
- Given a fake SSE MCP server "weather" on 127.0.0.1 with tool "forecast" returning "sunny", allowlisted
- When the LLM calls "weather__forecast" with city "Kyiv"
- Then the LLM receives the text "sunny"

### QA-003 List and call over stdio
- Covers: M1, B1
- Type: functional
- Given a fake stdio MCP server "local" started as a child process with tool "echo", allowlisted
- When the LLM calls "local__echo" with text "hello"
- Then the LLM receives the text "hello"

### QA-004 Unlisted tool is hidden
- Covers: E2, T4
- Type: functional
- Given server "notes" lists tools "add" and "delete_all", and the allowlist is ["add"]
- When the hub tool list is requested for a voice session
- Then "notes__add" is present and "notes__delete_all" is absent

### QA-005 Call to unlisted tool is unknown
- Covers: T4, E2
- Type: security
- Given the same setup as QA-004
- When the LLM calls "notes__delete_all"
- Then the LLM receives an unknown-tool error and the server never receives the call

### QA-006 Allowlist names a missing tool
- Covers: E3
- Type: edge
- Given server "notes" has only tool "add" and the allowlist is ["add", "ghost"]
- When the server starts
- Then a warning naming "ghost" is logged, "notes__add" works, and startup succeeds

### QA-007 Server down at startup
- Covers: E1
- Type: failure
- Given two configured servers, "up" (running, tool "ping") and "down" (nothing listens on 127.0.0.1 port 9)
- When the server starts
- Then startup completes, a warning for "down" is logged, "up__ping" and the device tools are listed, and no "down__" tools are listed

### QA-008 Down server stays off without restart
- Covers: E1
- Type: edge
- Given server "down" failed at startup and then begins listening
- When a new voice session lists tools
- Then "down__" tools are still not listed

### QA-009 Tool returns isError
- Covers: E4
- Type: failure
- Given a fake tool "boom" that returns an error with body "Traceback: password=hunter2 at /srv/app.py"
- When the LLM calls "notes__boom"
- Then the LLM gets an error outcome with a short generic text that does not contain "hunter2" or "/srv/app.py"

### QA-010 Image content replaced
- Covers: E5
- Type: edge
- Given a fake tool returning text "Chart:" and then a PNG image part of 40 KB
- When the LLM calls the tool
- Then the result is "Chart:" followed by the placeholder "[image]" and no image bytes

### QA-011 Resource content replaced
- Covers: E5
- Type: edge
- Given a fake tool returning one embedded resource part with uri "file:///a.txt"
- When the LLM calls the tool
- Then the result contains "[resource]" and no resource body

### QA-012 Invalid JSON config file
- Covers: E6
- Type: failure
- Given OTA_MCP_SERVERS_FILE points to a file with content "{not json"
- When the server starts
- Then an error is logged, no external servers are connected, and the device tools work

### QA-013 One bad entry skipped
- Covers: E6
- Type: failure
- Given a file with 3 entries: "a" valid, "b" with transport "ftp", "c" valid
- When the server starts
- Then an error for "b" is logged and tools of "a" and "c" are listed

### QA-014 Duplicate server name rejected
- Covers: E6
- Type: failure
- Given a file with two entries both named "notes" with different URLs
- When the server starts
- Then the duplicate is rejected with a logged error and only the first "notes" entry is connected

### QA-015 Server name fails name check
- Covers: E6, T5
- Type: failure
- Given entries named "my__srv", "bad name!" and a 70-character name
- When the server starts
- Then all 3 are rejected with logged errors and no tools from them are listed

### QA-016 Tool name fails name check
- Covers: T5
- Type: edge
- Given server "notes" with allowlisted tool "bad.tool" and a tool "ok_tool", both existing
- When tools are listed
- Then "notes__ok_tool" is listed, "notes__bad.tool" is not, and an error is logged

### QA-017 Combined name over 64 chars
- Covers: T5
- Type: edge
- Given server "notes" and a tool named with 60 characters
- When tools are listed
- Then the tool is dropped with a logged error because "notes__" plus the name exceeds 64 characters

### QA-018 Concurrent calls from two sessions
- Covers: E7
- Type: functional
- Given sessions A and B and a fake tool "slow_echo" that waits 0.5 s then returns its input
- When A calls it with "alpha" and B calls it with "beta" at the same time
- Then A gets "alpha", B gets "beta", and the server saw one connection

### QA-019 Timeout
- Covers: F1, M2
- Type: performance
- Given source timeout 2 s and a tool that sleeps 30 s
- When the LLM calls the tool
- Then the LLM gets a timeout error in 2.5 s or less

### QA-020 Call after timeout succeeds
- Covers: F1, E7
- Type: failure
- Given QA-019 just timed out and no reconnect happened
- When the LLM calls a fast tool "add" with a=1 and b=1
- Then the result is "2" and the connection count is still 1

### QA-021 HTTP server restart, reconnect
- Covers: F2, M3
- Type: failure
- Given an HTTP server connected, then stopped and started again on the same port between turns
- When the LLM calls "notes__add" with a=4 and b=4
- Then exactly 1 reconnect occurs and the result is "8"

### QA-022 Reconnect fails
- Covers: F2
- Type: failure
- Given an HTTP server connected and then stopped for good
- When the LLM calls "notes__add"
- Then the LLM gets an error outcome, the voice session stays open, and the next turn can still use device tools

### QA-023 Concurrent failed calls cause one reconnect
- Covers: F2, E7
- Type: edge
- Given a connected HTTP server that restarted, and two sessions
- When both call "notes__add" at the same time
- Then both succeed and exactly 1 reconnect is made

### QA-024 Stdio child exits
- Covers: F3
- Type: failure
- Given a stdio child that was killed between turns
- When the LLM calls "local__echo" with "again"
- Then the child restarts once and the result is "again"

### QA-025 Stdio child keeps crashing
- Covers: F3
- Type: failure
- Given a stdio child that exits at once on every start
- When the LLM calls "local__echo" twice
- Then each call gets an error outcome after at most one restart attempt, and the session survives

### QA-026 Shutdown leaves no children
- Covers: F4, M4
- Type: functional
- Given 2 stdio servers running (2 child processes) and 1 HTTP server connected
- When the server shuts down normally
- Then 0 stdio child processes remain

### QA-027 Shutdown with a child that ignores stop
- Covers: F4, M4
- Type: failure
- Given a stdio child that does not exit when its input closes
- When the server shuts down
- Then shutdown ends within about 5 s plus a small margin and 0 child processes remain

### QA-028 SDK exception becomes error
- Covers: F5
- Type: failure
- Given a fake server that sends a malformed protocol reply to a call
- When the LLM calls the tool
- Then the LLM gets an error outcome and no exception reaches the voice turn

### QA-029 Caller cancel (barge-in)
- Covers: F5, F1
- Type: edge
- Given a call to a tool that sleeps 30 s
- When the user barges in and the call is cancelled after 1 s
- Then the call stops at once with no error outcome returned, and a later call to "add" works

### QA-030 Missing config variable
- Covers: T3
- Type: edge
- Given OTA_MCP_SERVERS_FILE is not set
- When the server starts
- Then no external servers are connected, no error is logged, and device tools work

### QA-031 Config file path does not exist
- Covers: T3, E6
- Type: failure
- Given OTA_MCP_SERVERS_FILE is "/nonexistent/servers.json"
- When the server starts
- Then an error is logged and the server starts with device tools only

### QA-032 Timeout bounds
- Covers: T3
- Type: edge
- Given three entries with timeout 0.5, 61 and no timeout
- When the server starts
- Then the first two are rejected with logged errors and the third uses 10 s

### QA-033 URL with user info or query
- Covers: T8, T3
- Type: security
- Given entries with url "http://user:pw@127.0.0.1:8000/mcp" and "http://127.0.0.1:8000/mcp?token=abc"
- When the server starts
- Then both entries are rejected with logged errors and are not connected

### QA-034 URL with non-http scheme
- Covers: T3
- Type: failure
- Given an entry with transport http and url "file:///etc/passwd"
- When the server starts
- Then the entry is rejected with a logged error

### QA-035 Missing required fields
- Covers: T3
- Type: failure
- Given entries: stdio without "command", http without "url", and any entry without "tools"
- When the server starts
- Then all 3 are rejected with logged errors

### QA-036 Stdio child environment
- Covers: T7
- Type: security
- Given the server process has OPENAI_API_KEY=sk-test, OTA_TOKEN=abc and MCP_SECRET_X=zzz set, and an entry env {"MODE":"fast"}
- When a stdio tool prints the child environment
- Then it contains MODE=fast and PATH, and does not contain OPENAI_API_KEY, OTA_TOKEN or MCP_SECRET_X

### QA-037 Stdio command with its own args
- Covers: T3, B1
- Type: functional
- Given a stdio entry with command "python3" and args ["fake_server.py", "--name", "x"]
- When the tools are listed
- Then the child starts with those args and its tools are listed

### QA-038 Connections shared across sessions
- Covers: T6
- Type: functional
- Given 3 device sessions open in turn, with one stdio server
- When each session calls "local__echo"
- Then only 1 stdio child process exists at any time

### QA-039 Server with zero allowed tools
- Covers: T4, E3
- Type: edge
- Given an allowlist of ["ghost"] that matches no tool, and an empty allowlist [] on another server
- When the server starts
- Then neither server adds tools and, for stdio, the child is stopped

### QA-040 Long result truncated
- Covers: B2
- Type: edge
- Given a fake tool returning 10 000 characters of "x"
- When the LLM calls the tool
- Then the LLM receives at most 2000 characters, the same limit as for device tools

### QA-041 Device tools unchanged
- Covers: B2
- Type: functional
- Given a session with one device tool "light_on" and one external server
- When the LLM calls "light_on"
- Then it works as before and the tool list holds both device and external tools

### QA-042 Seam: connection denied
- Covers: B3
- Type: functional
- Given a policy that denies connections to server "notes"
- When the server starts
- Then "notes" is not connected, a warning is logged, and its tools are not listed

### QA-043 Seam: tool filter and extra headers
- Covers: B3
- Type: functional
- Given a policy that adds header "X-Test: 1" and a filter that rejects tool "add"
- When the server starts and the tools are listed
- Then the fake HTTP server sees "X-Test: 1" and "notes__add" is not listed

### QA-044 n8n-style entry needs config only
- Covers: B3
- Type: functional
- Given an http entry named "n8n" with a loopback URL and an allowlist of ["send_mail"]
- When the server starts
- Then "n8n__send_mail" is listed with no code change

### QA-045 Quality gate
- Covers: M5, T1, T2, T10
- Type: functional
- Given the finished change on a clean checkout
- When the gate script runs (lint, type check, tests)
- Then lint reports 0 findings, the type check reports 0 errors, all tests pass, and coverage is 90 % or more

### QA-046 SDK pin
- Covers: T2
- Type: functional
- Given the project dependency file and lock file
- When the MCP SDK entry is read in both
- Then both name the same single pinned version "1.30.0"

### QA-047 Tests use loopback only
- Covers: T9
- Type: security
- Given the full test suite runs with outbound network blocked except 127.0.0.1
- When the suite runs
- Then all MCP tests pass

### QA-048 Secrets absent from config
- Covers: T8
- Type: security
- Given a file with a stdio entry whose env has key "API_KEY" and value "sk-live-123"
- When the server starts
- Then the entry is accepted, because D8 makes secret values the owner's duty until #31 adds `MCP_SECRET_*`
- And the value "sk-live-123" does not appear in any log line

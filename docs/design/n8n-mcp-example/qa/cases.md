# QA cases: MCP server guide with n8n example (issue #29)

### QA-001 Guide length limit
- Covers: Success Metrics (guide at most 120 lines)
- Type: performance
- Given the guide `docs/guides/mcp-servers.md` is written
- When the owner counts its lines
- Then the count is 120 or less

### QA-002 Setup steps numbered 1 to 6 at most
- Covers: Technical Constraints (at most 6 numbered steps); Success Metrics
- Type: functional
- Given the guide has a setup section
- When the owner reads the numbered steps
- Then the numbers run 1, 2, 3 and so on with no gap, and the last number is 6 or less

### QA-003 Seven setup steps are rejected
- Covers: Success Metrics (steps 1 to at most 6)
- Type: edge
- Given a copy of the guide with a seventh numbered step "7."
- When the guide test runs on that copy
- Then the test fails and names the step count

### QA-004 Step numbering gap is rejected
- Covers: Success Metrics (steps numbered 1 to at most 6)
- Type: failure
- Given a copy of the guide with steps numbered 1, 2, 4
- When the guide test runs on that copy
- Then the test fails

### QA-005 JSON example loads with the real loader
- Covers: Success Metrics (JSON example loads without error)
- Type: functional
- Given the first json block of the guide is saved to a temporary servers file and `MCP_SECRET_N8N` is set to "test-secret-123"
- When the server loads that file
- Then the load ends without error and returns exactly 1 server entry

### QA-006 Example field values
- Covers: Success Metrics (JSON example); Technical Constraints (real config)
- Type: functional
- Given the same loaded example
- When the owner reads the entry
- Then it has an `Authorization` header that points to the env name `MCP_SECRET_N8N`, and the file holds no secret value

### QA-007 Example loads without the secret set
- Covers: Failure Modes (bearer secret missing)
- Type: failure
- Given the example servers file and `MCP_SECRET_N8N` not set in the environment
- When the server starts
- Then the n8n server is denied, other servers still start, and the server does not crash

### QA-008 Guide tells owner to set the secret in the environment
- Covers: Failure Modes (bearer secret missing)
- Type: functional
- Given the guide text
- When the owner reads the section on the bearer secret
- Then it tells the owner to set `MCP_SECRET_<NAME>` in the environment and never to write the secret in the servers file

### QA-009 Secret value in the servers file
- Covers: Failure Modes (bearer secret missing)
- Type: security
- Given the guide JSON block with the header value replaced by the text "Bearer abc123secret"
- When the guide test or the loader checks it
- Then the check fails, because a header must hold an env name that starts with `MCP_SECRET_`

### QA-010 Setting names exist in the code
- Covers: Success Metrics (setting names and env names exist)
- Type: functional
- Given the guide mentions `OTA_MCP_SERVERS_FILE` and `OTA_MCP_ALLOWED_HOSTS`
- When the guide test checks every `OTA_*` and `MCP_SECRET_*` name
- Then every name is found in the server settings or the MCP config, and the test passes offline with no network

### QA-011 Invented setting name is caught
- Covers: Success Metrics (setting names checked)
- Type: failure
- Given a copy of the guide that mentions `OTA_MCP_FAKE_SETTING`
- When the guide test runs on that copy
- Then the test fails and names `OTA_MCP_FAKE_SETTING`

### QA-012 Missing pin keeps the tool out
- Covers: Edge Cases (pin missing or wrong)
- Type: edge
- Given the owner connected the n8n server with a `tools` dict that has no entry for the tool "get_current_time"
- When the server starts and connects
- Then "get_current_time" is not offered to the model, and the log has a WARNING line with the tool name and `current hash sha256:` followed by 64 hex chars

### QA-013 Wrong pin keeps the tool out
- Covers: Edge Cases (pin missing or wrong)
- Type: edge
- Given the `tools` dict has "get_current_time" pinned to "sha256:" followed by 64 zeros
- When the server connects and n8n returns a different hash
- Then the tool is not admitted and the WARNING line shows the real current hash

### QA-014 Guide shows where to copy the hash
- Covers: Edge Cases (pin missing or wrong)
- Type: functional
- Given the guide text
- When the owner looks for how to get a pin
- Then the guide names the WARNING log line `current hash sha256:...` and says to copy the hash into `tools` and restart

### QA-015 Correct pin admits the tool
- Covers: Edge Cases (pin missing or wrong)
- Type: functional
- Given the owner copied the hash from the WARNING line into `tools` and restarted the server
- When the owner says "what time is it" and the model picks "get_current_time"
- Then the tool is available and its call runs after the confirm step

### QA-016 Pin format check
- Covers: Success Metrics (pin format checked)
- Type: edge
- Given a copy of the guide where a pin reads "sha256:1234" (4 hex chars)
- When the guide test runs on that copy
- Then the test fails, because a pin must be "sha256:" plus 64 hex chars

### QA-017 Edited description drops the tool
- Covers: Edge Cases (owner edits a tool description)
- Type: edge
- Given "get_current_time" is pinned and admitted, and the owner changes its description in n8n from "Return the time" to "Return the current time"
- When the server restarts and reconnects
- Then the tool is dropped and a WARNING line shows the new hash, until the owner updates the pin

### QA-018 Guide explains description edits
- Covers: Edge Cases (owner edits a tool description)
- Type: functional
- Given the guide text
- When the owner reads the Troubleshooting list
- Then one item says that a changed description changes the hash, the tool is dropped, and the owner must update the pin

### QA-019 Private LAN host blocked by default
- Covers: Edge Cases (private or LAN address)
- Type: security
- Given the n8n URL host is 192.168.1.50 and `OTA_MCP_ALLOWED_HOSTS` is empty
- When the server connects
- Then the connect is blocked and no n8n tool is offered

### QA-020 Allowlisted LAN host connects
- Covers: Edge Cases (private or LAN address)
- Type: functional
- Given `OTA_MCP_ALLOWED_HOSTS` is "192.168.1.50" and the URL host is 192.168.1.50
- When the server connects
- Then the SSRF check passes and the connect goes on

### QA-021 Guide explains the allowlist
- Covers: Edge Cases (private or LAN address)
- Type: functional
- Given the guide text
- When the owner reads the network step or Troubleshooting
- Then it says that private hosts are blocked unless listed in `OTA_MCP_ALLOWED_HOSTS` as a comma-separated list

### QA-022 Plain http to a non-allowlisted host
- Covers: Edge Cases (private or LAN address); docs/security.md (http rule)
- Type: security
- Given the URL is "http://n8n.example.com/mcp" and the host is not in the allowlist
- When the server connects
- Then the connect is refused because that host needs https

### QA-023 Redirect from n8n
- Covers: Edge Cases (private or LAN address); docs/security.md (no redirects)
- Type: security
- Given the n8n host answers with an HTTP 302 redirect
- When the server connects
- Then the connect fails and the n8n tools are not offered

### QA-024 no_confirm key not in tools
- Covers: Edge Cases (no_confirm not a key of tools)
- Type: failure
- Given a servers file with `"tools": {"get_current_time": "sha256:<64 hex>"}` and `"no_confirm": ["delete_all"]`
- When the server loads the file
- Then the load of that entry fails with an error that names the unknown tool "delete_all"

### QA-025 Guide shows a valid no_confirm example
- Covers: Edge Cases (no_confirm not a key of tools)
- Type: functional
- Given the guide text
- When the owner reads the config section
- Then each name in `no_confirm` is also a key of `tools` in the example

### QA-026 Guide test checks no_confirm keys
- Covers: Edge Cases (no_confirm); Success Metrics
- Type: failure
- Given a copy of the guide where `no_confirm` lists "ghost_tool" that is not in `tools`
- When the guide test runs on that copy
- Then the test fails

### QA-027 n8n down at start
- Covers: Failure Modes (n8n is down at start)
- Type: failure
- Given the n8n host does not answer
- When the server starts
- Then the connect gives up after 5 s, the server starts, and the n8n tools are missing with no error shown to the user

### QA-028 Guide explains n8n down
- Covers: Failure Modes (n8n is down at start)
- Type: functional
- Given the guide text
- When the owner reads Troubleshooting
- Then an item says that with n8n down the tool is missing, it is not an error for the user, and the owner restarts the hub after n8n is up

### QA-029 Owner skips the pin step
- Covers: Failure Modes (owner skips the pin step)
- Type: failure
- Given the owner set up steps 1 to 4 and skipped the pin step
- When the owner says "what time is it"
- Then the model has no n8n tool, the hub does not call n8n, and the hub says nothing about it

### QA-030 Troubleshooting list covers each symptom
- Covers: Failure Modes (owner skips the pin step); Success Metrics
- Type: functional
- Given the guide text
- When the owner reads the Troubleshooting list
- Then it has one item each for: tool missing (pin), tool dropped (changed description), host blocked, server denied (secret not set), n8n down, and each item gives a fix

### QA-031 Guide test checks Troubleshooting section
- Covers: Success Metrics; decision.md (strings for troubleshooting)
- Type: failure
- Given a copy of the guide with the Troubleshooting heading removed
- When the guide test runs on that copy
- Then the test fails

### QA-032 Risky tool asks to confirm
- Covers: Business Goals (risky tool asks to confirm)
- Type: functional
- Given an admitted n8n tool "send_email" that is not in `no_confirm`, and language `en`
- When the owner says "send an email to Anna with the report"
- Then the hub speaks "Should I run n8n send_email ...? Say yes to confirm." and the tool does not run yet

### QA-033 Confirmed tool runs once
- Covers: Business Goals (one n8n MCP tool works)
- Type: functional
- Given the confirm question for "send_email" was spoken
- When the owner presses the button and says "Yes."
- Then the tool runs once and the hub speaks "Done."

### QA-034 Wrong answer cancels
- Covers: Business Goals (risky tool asks to confirm)
- Type: failure
- Given the confirm question for "send_email" was spoken
- When the owner says "yes please"
- Then the tool does not run and the hub speaks "Cancelled."

### QA-035 No answer in 10 s
- Covers: Business Goals (risky tool asks to confirm); docs/protocol.md (timeout)
- Type: failure
- Given the confirm question was spoken and the owner does not press the button
- When 10 s pass after the reply ends
- Then the held call is dropped and the device gets the error code `confirm_timeout`

### QA-036 no_confirm tool runs without a question
- Covers: Business Goals; docs/security.md (no_confirm)
- Type: functional
- Given "get_current_time" is pinned and listed in `no_confirm`
- When the owner says "what time is it"
- Then the tool runs and the hub speaks the answer with no confirm question

### QA-037 Guide states any MCP server works
- Covers: Business Goals (n8n is the example, any MCP server works)
- Type: functional
- Given the guide text
- When the owner reads the introduction
- Then it names n8n as the worked example and states that any MCP server works the same way

### QA-038 Guide links in README
- Covers: Technical Constraints (README link next to speech guide); tasks T1
- Type: functional
- Given `README.md` is updated
- When the owner searches for "guides/mcp-servers.md"
- Then exactly 1 link is found, and it is next to the speech guide link

### QA-039 No invented n8n identifiers
- Covers: Success Metrics (zero invented n8n identifiers); Technical Constraints
- Type: failure
- Given a copy of the guide that holds an invented n8n node type id, for example "n8n-nodes-base.mcpMagicTrigger"
- When the guide test runs on that copy
- Then the test fails on the denylist

### QA-040 Unconfirmed n8n facts are marked
- Covers: Technical Constraints (mark node type id, URL path, JSON shape)
- Type: functional
- Given the guide text
- When the owner looks for the node type id, the URL path and the workflow JSON shape
- Then each one is marked "see your n8n UI" and no exact value is given

### QA-041 n8n facts match the docs
- Covers: Technical Constraints (state only confirmed n8n facts)
- Type: functional
- Given the guide text
- When the owner reads the n8n facts
- Then they list SSE and streamable HTTP, auth none, bearer, header or OAuth2, and the Custom n8n Workflow Tool node, and nothing more
- OPEN: no live n8n instance exists, so these facts cannot be checked by running. The user accepted this risk.

### QA-042 No workflow JSON or new settings
- Covers: Technical Constraints (no code, no workflow JSON, no new settings)
- Type: functional
- Given the change set for issue #29
- When the owner lists the changed files
- Then they are the guide, the README, and the guide test, with no n8n workflow JSON file and no new setting name

### QA-043 Gate passes
- Covers: Success Metrics (gate with 90 percent coverage)
- Type: performance
- Given the guide and the test are in the repo
- When the owner runs the gate script (ruff, strict mypy, pytest)
- Then all three pass and coverage is 90 percent or more

### QA-044 Guide test runs offline
- Covers: Success Metrics (passes offline)
- Type: functional
- Given the machine has no network
- When the owner runs the guide test file
- Then all cases pass and no network call is made

### QA-045 Repeated pin copy
- Covers: Edge Cases (pin missing or wrong)
- Type: edge
- Given the owner pasted the same hash for "get_current_time" twice in `tools`, with the same key repeated
- When the server loads the file
- Then the load either takes the last value or fails with a clear error, and the hub does not crash
- OPEN: the requirements do not say how a repeated key is handled.

### QA-046 Same secret name in two entries
- Covers: Failure Modes (bearer secret); docs/security.md (one name in one entry)
- Type: failure
- Given two server entries that both use `MCP_SECRET_N8N`
- When the server loads the file
- Then the second entry is rejected

### QA-047 Entry named device
- Covers: docs/security.md (reserved name)
- Type: security
- Given a server entry named "device"
- When the server loads the file
- Then that entry is rejected

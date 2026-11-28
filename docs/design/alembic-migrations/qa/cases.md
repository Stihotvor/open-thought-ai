# QA cases: Alembic migrations (issue #32)

### QA-001 Upgrade, downgrade one step, upgrade again on Postgres 16
- Covers: Success Metric 1, T3
- Type: functional
- Given an empty Postgres 16 database and DEVICE_ID `abc_1`
- When the runner does upgrade to head, then downgrade one step with the downgrade flag, then upgrade to head
- Then all three runs exit 0 and the schema `ota_abc_1` holds all tables from the data model at the end

### QA-002 Two parallel runners
- Covers: Success Metric 2, Edge Case 2
- Type: functional
- Given an empty Postgres 16 database and DEVICE_ID `abc_1`
- When two migrate processes start at the same moment
- Then both exit 0 and the version table in `ota_abc_1` holds exactly 1 row

### QA-003 Failed migration leaves no tables
- Covers: Success Metric 3, Failure Mode 1
- Type: failure
- Given an empty schema and a test migration whose last statement is invalid SQL
- When migrate runs
- Then the exit code is not 0 and the schema `ota_abc_1` holds 0 tables and 0 version rows

### QA-004 Failed migration frees the lock and blocks server
- Covers: Failure Mode 1, Technical Constraint 4
- Type: failure
- Given the failure setup of QA-003 and the compose stack
- When migrate exits non-zero and a second session then asks for the same lock
- Then the second session gets the lock at once and the server container does not start

### QA-005 Tests skip without a test database
- Covers: Success Metric 4
- Type: functional
- Given a machine without Docker and with `OTA_TEST_DATABASE_URL` unset
- When the full server test suite and the gate script run
- Then the integration tests show as skipped and the exit code is 0

### QA-006 No database URL in logs on success
- Covers: Success Metric 5, Technical Constraint 7
- Type: security
- Given `OTA_DATABASE_URL=postgresql://ota:s3cretpw@db:5432/ota` and a working database
- When migrate runs and all log output is captured
- Then no log line contains `s3cretpw` or the full URL

### QA-007 No database URL in logs on failure
- Covers: Success Metric 5, Failure Mode 3
- Type: security
- Given `OTA_DATABASE_URL=postgresql://ota:s3cretpw@unreachable-host:5432/ota`
- When migrate runs and cannot connect
- Then the exit code is not 0, a log line says the database is unreachable, and no log line contains `s3cretpw`

### QA-008 Settings do not show the URL
- Covers: Success Metric 5, Technical Constraint 7
- Type: security
- Given `OTA_DATABASE_URL=postgresql://ota:s3cretpw@db:5432/ota` in env
- When the settings object is printed as text
- Then the output does not contain `s3cretpw`

### QA-009 Second run changes nothing
- Covers: Edge Case 1
- Type: edge
- Given `ota_abc_1` is already at head with 1 version row
- When migrate runs again
- Then it exits 0, the version row is unchanged, and the `settings` row and the `device` row of `mcp_server` still count 1 each

### QA-010 Two instances share one database
- Covers: Edge Case 3
- Type: edge
- Given DEVICE_ID `alpha` and DEVICE_ID `beta` on one database, and a migration run for `alpha` that is held in its lock wait
- When migrate for `beta` runs
- Then `beta` finishes without waiting, and `ota_alpha` and `ota_beta` each hold their own version table with 1 row

### QA-011 Lock wait longer than 10 s
- Covers: Edge Case 4, Technical Constraint 3
- Type: edge
- Given the role has `statement_timeout` of 10 s and another session holds the lock for 15 s
- When a migrate process waits for the lock
- Then it is not cancelled at 10 s and exits 0 after the holder ends, with nothing left to do

### QA-012 Empty DEVICE_ID
- Covers: Edge Case 5, Technical Constraint 5
- Type: edge
- Given DEVICE_ID is an empty string
- When migrate or the settings load runs
- Then it is rejected with a message about the allowed pattern and no schema is created

### QA-013 DEVICE_ID of 41 characters
- Covers: Edge Case 5
- Type: edge
- Given DEVICE_ID is `a` repeated 41 times
- When the schema name is built
- Then it is rejected and nothing is sent to the database

### QA-014 DEVICE_ID of 40 characters is accepted
- Covers: Edge Case 5
- Type: edge
- Given DEVICE_ID is `a` repeated 40 times
- When the schema name is built
- Then it gives the quoted name `"ota_aaaa..."` with 40 `a` after `ota_`

### QA-015 DEVICE_ID with upper-case letters
- Covers: Edge Case 5
- Type: edge
- Given DEVICE_ID is `Kitchen`
- When the schema name is built
- Then it is rejected

### QA-016 DEVICE_ID with a quote
- Covers: Edge Case 5, Technical Constraint 5
- Type: security
- Given DEVICE_ID is `a"; DROP SCHEMA public; --`
- When migrate runs
- Then it is rejected before any connection and the `public` schema is untouched

### QA-017 Valid DEVICE_ID is quoted
- Covers: Technical Constraint 5
- Type: functional
- Given DEVICE_ID is `abc_1`
- When the schema name is built
- Then the result is `"ota_abc_1"`

### QA-018 Schema does not exist
- Covers: Edge Case 6, Technical Constraint 2
- Type: edge
- Given a database where `ota_abc_1` does not exist and the user has CREATE on the database
- When migrate runs
- Then the schema is created, all tables exist in it, and the exit code is 0

### QA-019 Schema already exists
- Covers: Edge Case 6, Technical Constraint 2
- Type: edge
- Given `ota_abc_1` exists and is empty, and the user has no CREATE on the database
- When migrate runs
- Then it exits 0 with no permission error and all tables exist

### QA-020 Version table lives in the instance schema
- Covers: Technical Constraint 1
- Type: functional
- Given a finished upgrade for `abc_1`
- When the schemas of the database are listed
- Then the version table is in `ota_abc_1` and the `public` schema has no version table

### QA-021 Crashed runner frees the lock
- Covers: Failure Mode 2
- Type: failure
- Given a migrate process that holds the lock and is killed with SIGKILL
- When a new migrate process starts
- Then it gets the lock without waiting for the 5 minute bound and exits 0

### QA-022 Postgres unreachable
- Covers: Failure Mode 3
- Type: failure
- Given the database host is stopped
- When migrate runs
- Then it exits non-zero and does not hang

### QA-023 Lock wait bound exceeded
- Covers: Failure Mode 4, Decision rule 3
- Type: failure
- Given another session holds the lock for longer than 5 minutes
- When migrate waits
- Then after 5 minutes it exits non-zero and logs a timeout line without the URL

### QA-024 Server refuses to start on a revision mismatch
- Covers: Failure Mode 5, Decision rule 9
- Type: failure
- Given the database holds revision `0000_old` and the code head is `0001_initial`
- When the server starts
- Then it stops and logs one line with both `0000_old` and `0001_initial` and no URL

### QA-025 Server refuses to start when schema is missing
- Covers: Failure Mode 5, Decision rule 9
- Type: failure
- Given `OTA_DATABASE_URL` is set and schema `ota_abc_1` does not exist
- When the server starts
- Then it stops with a revision mismatch log line and no raw database error text

### QA-026 Server refuses to start when version table is missing
- Covers: Failure Mode 5, Decision rule 9
- Type: failure
- Given schema `ota_abc_1` exists and has no version table
- When the server starts
- Then it stops with a revision mismatch log line and no raw database error text

### QA-027 Server starts at head
- Covers: Failure Mode 5
- Type: functional
- Given the database revision equals the code head
- When the server starts
- Then it starts and serves requests

### QA-028 Startup check is off without a URL
- Covers: Decision rule 9
- Type: edge
- Given `OTA_DATABASE_URL` is not set
- When the server starts
- Then it starts and makes no database connection

### QA-029 Downgrade needs the flag
- Covers: Failure Mode 6, Decision rule 7
- Type: failure
- Given `ota_abc_1` is at head
- When the CLI is asked for downgrade without `--allow-downgrade`
- Then it exits non-zero, the revision is unchanged, and all tables remain

### QA-030 Downgrade with the flag
- Covers: Failure Mode 6
- Type: functional
- Given `ota_abc_1` is at head
- When the CLI runs downgrade with `--allow-downgrade`
- Then it exits 0 and the tables of the first migration are gone

### QA-031 First migration seeds settings
- Covers: Technical Constraint 6, Decision rule 11
- Type: functional
- Given a finished upgrade
- When the `settings` table is read
- Then it has 1 row with language `en`, voice empty, system prompt empty, volume 50, retention_days 90, memory_retention_days 365, speech_cap_s 60

### QA-032 Settings accepts only one row
- Covers: Technical Constraint 6
- Type: edge
- Given a seeded `settings` row
- When a second row is inserted with `id` false
- Then the insert is rejected

### QA-033 Built-in device row is seeded once
- Covers: Technical Constraint 6, Decision rule 10
- Type: functional
- Given a finished upgrade, then a second upgrade
- When `mcp_server` is read
- Then it has 1 row named `device` with transport `device` and no url

### QA-034 Second device row is refused
- Covers: Technical Constraint 6
- Type: edge
- Given the seeded `device` row
- When a row named `device2` with transport `device` and no url is inserted
- Then the insert is rejected

### QA-035 Device transport with a url is refused
- Covers: Technical Constraint 6
- Type: failure
- Given an upgraded schema
- When an `mcp_server` row with transport `sse` and no url is inserted
- Then the insert is rejected

### QA-036 Turn role check
- Covers: Technical Constraint 6
- Type: failure
- Given a conversation row exists
- When a turn with role `system` is inserted
- Then the insert is rejected

### QA-037 Turn needs content or tool calls
- Covers: Technical Constraint 6
- Type: failure
- Given a conversation row exists
- When a turn with role `user`, content null and tool_calls null is inserted
- Then the insert is rejected

### QA-038 Negative latency refused
- Covers: Technical Constraint 6
- Type: failure
- Given a conversation row exists
- When a turn with latency_ms -1 is inserted
- Then the insert is rejected

### QA-039 Conversation end before start refused
- Covers: Technical Constraint 6
- Type: failure
- Given an upgraded schema
- When a conversation with started_at 2026-11-27 10:00 and ended_at 2026-11-27 09:00 is inserted
- Then the insert is rejected

### QA-040 Conversation delete cascades to turns
- Covers: Technical Constraint 6
- Type: functional
- Given one conversation with 3 turns
- When the conversation is deleted
- Then 0 turns remain for it

### QA-041 Settings range checks
- Covers: Technical Constraint 6
- Type: failure
- Given the seeded `settings` row
- When volume is set to 101, language to `de`, or speech_cap_s to 4
- Then each update is rejected

### QA-042 Secret header values must be env names
- Covers: Technical Constraint 6
- Type: security
- Given an `mcp_server` row with transport `sse` and a url
- When secret_headers is set to `{"Authorization": "Bearer abc123"}`, or to the JSON array `["x"]`
- Then each write is rejected, and `{"Authorization": "MCP_SECRET_N8N"}` is accepted

### QA-043 Tool name unique per server
- Covers: Technical Constraint 6
- Type: failure
- Given one `tool` named `search` for one server
- When a second tool named `search` is inserted for the same server
- Then the insert is rejected
- OPEN: the data model lists the `MCP_SECRET_` prefix rule as a constraint, but does not say if the database or only the server enforces it. QA-042 assumes the database.

### QA-044 New tool defaults
- Covers: Technical Constraint 6
- Type: functional
- Given an `mcp_server` row
- When a tool is inserted with only its name and server
- Then enabled is false and confirm is true

### QA-045 Turn id is generated
- Covers: Technical Constraint 6
- Type: edge
- Given a conversation row
- When a turn insert gives its own id 5
- Then the insert is rejected

### QA-046 Migrate does not touch roles
- Covers: Technical Constraint 10
- Type: edge
- Given a finished upgrade
- When roles and grants of the database are listed
- Then no role `ota_abc_1` was created by migrate

### QA-047 Compose order
- Covers: Technical Constraint 4
- Type: functional
- Given the compose file
- When the compose config is shown
- Then `server` depends on `migrate` with condition `service_completed_successfully`

### QA-048 Test profile database
- Covers: Technical Constraint 9
- Type: functional
- Given the compose file
- When `docker compose --profile test up -d postgres-test` runs
- Then a container with image `postgres:16-alpine` is running

### QA-049 Migrate through a pooler is not supported
- Covers: Open item (PgBouncer), Decision rule 1
- Type: edge
- Given the OPEN item about PgBouncer on the home stack
- When migrate is pointed at a pooler in transaction mode
- Then the result is not defined
- OPEN: the requirements do not say what migrate must do through a pooler. It only says it connects directly.

### QA-050 Migration time on an empty schema
- Covers: Success Metric 1 (time bound not stated)
- Type: performance
- Given an empty Postgres 16 schema
- When upgrade to head runs
- Then it ends in under 30 s
- OPEN: no requirement gives a time limit. The 30 s value is a QA proposal.

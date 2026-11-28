# Requirements: Alembic migrations (issue #32)

Source: `docs/data-model.md` (Migrations, Tables, Constraints) and `docs/delivery-plan.md` (M5). Size: M.

## Business Goals
- Create and update the per-instance Postgres schema with one command that is safe to run twice and safe to run from two copies.
- Give #33 (role setup), #34 (backup), #35 (pages), #36 (login) and #37 (retention) a stable schema to build on.

## Technical Constraints
1. Per-schema version table: `version_table_schema = ota_<device_id>`.
2. The schema exists before Alembic reads the version table. `migrate` creates it only if it is missing, under the lock (in production #33 creates it). Role and grants belong to #33.
3. The upgrade runs under a Postgres advisory lock derived from the schema name. A second runner waits, then finds nothing to do.
4. A one-shot compose service `migrate`. `server` starts only after it exits 0 (`service_completed_successfully`).
5. `DEVICE_ID` matches `^[a-z0-9_]{1,40}$`. One function builds and quotes the schema name. Models and migrations hold no schema name.
6. The first migration creates all tables, constraints and indexes in `docs/data-model.md` and seeds the `settings` row and the built-in `device` row in `mcp_server`.
7. Settings come from env (`OTA_` prefix). The database URL never appears in logs.
8. Project dependencies added with `uv add`: `alembic`, `sqlalchemy`, `psycopg[binary]` (user approved, 2026-11-27).
9. Test database: `postgres:16-alpine`, in CI and in a compose test profile (user decision, 2026-11-27).
10. Out of scope: roles and grants (#33), backup (#34), pages (#35), login (#36), retention job (#37), Qdrant. The server does not read the new tables.

## Edge Cases
- `migrate` runs twice in a row: the second run changes nothing.
- Two `migrate` processes start together: one waits, both exit 0, one revision row.
- Two instances (different `DEVICE_ID`) share one database: separate schemas, separate version tables, no lock contention.
- A waiting runner waits longer than the role `statement_timeout` (10 s).
- `DEVICE_ID` is empty, too long, or has upper-case letters or a quote.
- The schema already exists, or does not exist yet (dev and CI).

## Failure Modes
- A migration statement fails: the transaction rolls back, no table remains, exit code is not 0, the lock is released, `server` does not start.
- The runner crashes: the session ends and the lock is freed.
- Postgres is unreachable: exit non-zero with a log line that has no URL.
- The lock wait exceeds its bound: exit non-zero.
- The database revision differs from the code head when `server` starts: `server` refuses to start.
- A downgrade run by accident: needs an explicit flag.

## Success Metrics
- CI runs `upgrade head`, `downgrade -1`, `upgrade head` on Postgres 16 and passes.
- CI runs two parallel runners: both exit 0 and the version table has one row.
- A failed migration leaves no tables (tested).
- `gate.sh` stays green without Docker (integration tests skip without `OTA_TEST_DATABASE_URL`).
- No database URL in any log line (tested).

## Open
- OPEN: the Postgres version and any PgBouncer on the home stack. Tests use 16. `migrate` connects directly.

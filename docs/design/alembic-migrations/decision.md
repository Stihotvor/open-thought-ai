# Decision: Alembic migrations (issue #32)

## Context
See `requirements.md`. The server has no database code. `docs/data-model.md` plans Alembic with a per-schema version table, an advisory lock and a `migrate` compose service. The dba agent reviewed four paths (2026-11-27).

## Options
1. Stock Alembic CLI with `env.py` logic. Feasible with change.
2. Library function `migrate(settings)` with a thin CLI. Feasible with change.
3. Migration inside the server lifespan. Not feasible: the runtime role would need DDL rights, the 10 s role timeout kills it, and every worker would run it.
4. SQL-first revisions. Feasible, but down files drift and the cost is higher.

## Decision
Path 2, plus a read-only startup check from path 4. The user chose this at G1 (2026-11-27).

Rules the build must follow (from the dba review):
1. One connection, `NullPool`, direct to Postgres (never through a pooler).
2. After the lock is acquired, the session sets finite backstops: `statement_timeout=30min` and `idle_in_transaction_session_timeout=10min`. The try-lock polling needs no override.
3. The lock comes first: `pg_try_advisory_lock(<fixed int4>, hashtext(<schema>))`, polled for a bounded time (5 min), with the schema name as a bound parameter. When the wait runs out, `migrate` exits non-zero with a log line that has no URL. Then `COMMIT` (SQLAlchemy 2 autobegin), so Alembic opens its own transaction.
4. Under the lock: create the schema only if `to_regnamespace` returns null. Commit. In production #33 creates the schema, so `migrate` needs no CREATE privilege there. In dev and CI `migrate` needs CREATE on the database.
5. Then `lock_timeout` for DDL, then `command.upgrade` with `attributes['connection']`, `version_table_schema=ota_<id>`, `transaction_per_migration=False`.
6. `finally`: `rollback()`, then `pg_advisory_unlock`, then close.
7. `downgrade` is only reachable through an explicit flag. CI uses it.
8. `pg_dump -n` before a migration is NOT in this issue. It belongs to the backup work (#34). The `migrate` entrypoint will call it.
9. Startup check: `server` reads `alembic_version` and compares it with the code head. A mismatch, a missing schema, or a missing `alembic_version` table all count as a mismatch (no raw database error). The check stops the start with a log line that holds both revisions and no URL. Without `OTA_DATABASE_URL` the check is off (the server does not use the database yet).
10. First migration DDL: as `docs/data-model.md`, with every constraint named, no `pgcrypto`, `gen_random_uuid()` from core, one transaction, no `CONCURRENTLY`. Seeds use `ON CONFLICT DO NOTHING`.
11. `settings` defaults (NOT NULL): `language 'en'`, `voice ''`, `system_prompt ''`, `volume 50`. The row is seeded with `DEFAULT VALUES`. The built-in row in `mcp_server` is named `device`.
12. Update `docs/data-model.md`: two-key lock, lock before schema, CREATE-on-database note, migration session timeouts, direct connection, the new `settings` defaults, and move the "pg_dump -n before each migration" line to #34 (safe: 0001 runs on an empty schema).

## Consequences
- CI tests: two parallel runners give one result. A runner that waits longer than 10 s is not cancelled. A failed migration leaves no tables and no lock.
- `ota_server.db` becomes the one place for names, settings, engine and migration code. #33 and #34 call it.
- New dependencies: `alembic`, `sqlalchemy`, `psycopg[binary]`.
- CI gets a Postgres 16 job. `gate.sh` stays green without Docker.
- OPEN for #33: split an owner role (DDL, used by `migrate`) from a runtime role (DML only).
- OPEN: home stack Postgres version and any PgBouncer.
- The image must carry `pg_dump` at the server major version before #34 uses it.

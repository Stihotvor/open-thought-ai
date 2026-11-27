# Path 1: stock Alembic CLI
Lens: standard Alembic layout. Specific work sits in `env.py`.

- Files: `server/alembic.ini` (script_location only), `server/migrations/env.py`, `server/migrations/versions/0001_initial.py`, one names function, one settings module with a masked URL.
- Compose: `migrate` service, same image, command `alembic upgrade head`, `restart: "no"`. `server` waits with `service_completed_successfully`.
- Flow: validate `DEVICE_ID`, open one connection with `search_path`, `CREATE SCHEMA IF NOT EXISTS` and commit, take session `pg_advisory_lock`, run migrations with `version_table_schema`, unlock in `finally`.
- Failure: transactional DDL rolls back, non-zero exit, session end frees the lock.
- Not handled: no lock timeout, no startup revision check (compose order is the guard).
- Effort: M. Risk: lock must sit on the migration connection (use NullPool). Alembic transaction handling needs a CI test.
- Choose when: the least surprising setup matters most.

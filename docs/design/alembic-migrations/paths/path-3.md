# Path 3: migration inside the server (monolith)
Lens: `run_upgrade()` runs in the FastAPI lifespan before `yield`. The `migrate` service uses the same function.

- Files: `db/{names,migrate,engine}.py`, settings, Alembic env and `0001_initial.py`, lifespan change in the app.
- Flow: lock, `CREATE SCHEMA IF NOT EXISTS`, upgrade, unlock. The server calls it again and finds nothing to do. Bounded `pg_try_advisory_lock` polling gives a lock timeout.
- Fit: one path that cannot be bypassed. Startup revision check is implicit.
- Risks: the runtime role needs DDL rights (conflicts with least privilege in #33). A slow migration blocks startup. The lifespan path is the least tested.
- Effort: M.
- Choose when: bypass-proof migration matters more than least privilege.

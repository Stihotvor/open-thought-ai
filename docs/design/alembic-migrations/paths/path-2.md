# Path 2: library API (hexagonal)
Lens: one tested function `migrate(settings)`. The CLI, the role setup script (#33) and tests are thin adapters.

- Files: `ota_server/db/{names,settings,engine,migrate}.py`, `ota_server/db/alembic/{env.py,script.py.mako,versions/0001_initial.py}`.
- `env.py` reads `config.attributes['connection']` and holds no URL and no schema name.
- Compose: `migrate` runs `python -m ota_server.db.migrate`. `server` waits for it.
- Flow: names check, one connection, `CREATE SCHEMA IF NOT EXISTS` and commit, session lock, `command.upgrade` on that connection, unlock in `finally`.
- Tests: unit tests with a fake connection and a mocked `alembic.command`. Integration tests call `migrate()` directly (threads for the parallel case).
- Not handled: no lock timeout, no startup revision check (a helper can come later).
- Effort: M. Risk: `command.upgrade` on a connection that holds a lock and has committed once. Pool must not swap connections.
- Choose when: later code (#33) must call migration code directly.

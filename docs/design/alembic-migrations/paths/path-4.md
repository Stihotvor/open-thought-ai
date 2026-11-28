# Path 4: SQL-first
Lens: Alembic is only the revision ledger. Each revision is a thin `.py` that runs `NNNN_name.up.sql` and `.down.sql`.

- Files: `migrations/{env.py,sql/0001_initial.{up,down}.sql,versions/0001_initial.py}`, `db/{names,settings,sql_runner,schema_guard}.py`.
- `schema_guard` compares the DB revision with the code head. `server` refuses to start on a mismatch.
- Flow: as path 1 (schema, lock, upgrade in one transaction). CI also diffs `pg_dump --schema-only` before and after up/down/up.
- Fit: the DDL reads and diffs directly against `docs/data-model.md`.
- Risks: hand-written down files can drift. Multi-statement SQL splitting. Revision id repeated in file names (a pair lint reduces this). No autogenerate.
- Effort: M.
- Choose when: reviewable raw SQL matters more than Alembic helpers.

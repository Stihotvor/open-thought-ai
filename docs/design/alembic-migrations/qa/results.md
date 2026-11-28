# QA results: Alembic migrations (issue #32)

Working directory: /home/yevhend/claude-projects/open-thought-ai

Run (2026-11-28): the main session ran the full suite against Postgres 16 (`postgres:16-alpine`, port 55432) with `OTA_TEST_DATABASE_URL` set: 1029 passed, 1 skipped. The 45 integration tests all pass. Five of them first failed because of a bug in the test helper (no commit, no row check). The helper was fixed. No product defect was found. Without the variable the integration tests skip and `gate.sh` stays green (984 passed, 11 skipped).

Counts: pass 43, partial 5, not-automated 2 (QA-004, QA-049), fail 0 (total 50).

Checked by hand in the main session: QA-047 (`docker compose config` shows `server` depends on `migrate` with `service_completed_successfully`) and QA-048 (`postgres-test` appears only with `--profile test`). Both count as pass. QA-004 stays not-automated.

New file: /home/yevhend/claude-projects/open-thought-ai/server/tests/integration/test_qa_alembic_pg.py (named test_qa_NNN_*).
Existing: /home/yevhend/claude-projects/open-thought-ai/server/tests/integration/test_migrations_pg.py (E-pg), tests/test_db_*.py (unit).

| Case | Status | Test or reason |
|---|---|---|
| QA-001 | pass | E-pg::test_upgrade_downgrade_upgrade (checks exit via no exception; table set) |
| QA-002 | pass | E-pg::test_two_parallel_runners (threads, not processes) |
| QA-003 | pass | E-pg::test_failed_migration_leaves_nothing |
| QA-004 | not-automated | needs the compose stack (server container must not start); lock freeing is in QA-003 test |
| QA-005 | partial | integration tests skip without the env var (observed: 45 skipped, rest pass); the gate script was not run |
| QA-006 | pass | test_qa_006_no_url_in_logs_on_success |
| QA-007 | pass | test_db_migrate.py::test_unreachable_database, test_db_cli.py::test_cli_failure_exit_and_no_url; also test_qa_007_unreachable_has_no_password (pass, no DB needed in practice) |
| QA-008 | pass | test_db_settings.py::test_database_url_is_read_and_hidden |
| QA-009 | pass | test_qa_009_second_run_keeps_seed_rows (E-pg::test_second_run_changes_nothing covers the version row) |
| QA-010 | pass | test_qa_010_two_instances_share_one_database |
| QA-011 | pass | E-pg::test_long_wait_is_not_cancelled |
| QA-012 | pass | test_db_names.py::test_invalid_device_id_is_rejected[]; no-schema check in test_qa_012 (pass) |
| QA-013 | pass | test_db_names.py::test_invalid_device_id_is_rejected[a*41] |
| QA-014 | pass | test_db_names.py::test_forty_character_device_id_is_accepted |
| QA-015 | pass | test_db_names.py::test_invalid_device_id_is_rejected[Abc] |
| QA-016 | pass | test_db_names.py (quote rejected); test_qa_016 (pass) adds the public-untouched check |
| QA-017 | pass | test_db_names.py::test_valid_device_id_gives_quoted_schema_name |
| QA-018 | pass | test_qa_018_schema_is_created |
| QA-019 | pass | test_qa_019_existing_schema_needs_no_create_privilege |
| QA-020 | pass | test_qa_020_version_table_in_instance_schema |
| QA-021 | pass | test_qa_021_killed_holder_frees_the_lock (uses pg_terminate_backend on the lock holder, not SIGKILL of a migrate process) |
| QA-022 | partial | test_db_migrate.py::test_unreachable_database (fake connect); test_qa_022 (pass) uses a closed port and a time bound; a stopped host was not tested |
| QA-023 | partial | test_db_migrate.py::test_lock_wait_exceeds_bound and E-pg::test_lock_wait_gives_up use a 0.05 to 0.5 s bound; the real 5 minute bound is not exercised |
| QA-024 | pass | test_db_startup_check.py::test_start_refused[differs]; real-DB variant test_qa_024 (pass) |
| QA-025 | pass | test_db_startup_check.py::test_start_refused[no_schema]; real-DB variant test_qa_025 (pass) |
| QA-026 | pass | test_db_startup_check.py::test_start_refused[no_table]; real-DB variant test_qa_026 (pass) |
| QA-027 | pass | test_db_startup_check.py::test_revision_matches; real-DB variant test_qa_027 (pass); "serves requests" not checked |
| QA-028 | pass | test_db_startup_check.py::test_no_url_opens_no_connection, test_lifespan_off_without_url |
| QA-029 | pass | test_db_cli.py::test_downgrade_needs_flag, test_db_migrate.py::test_downgrade_needs_explicit_flag; real-DB test_qa_029_030 (pass) |
| QA-030 | pass | test_db_cli.py::test_downgrade_with_flag_is_passed; real-DB test_qa_029_030 (pass) |
| QA-031 | pass | test_qa_031_settings_seed |
| QA-032 | pass | test_qa_032_settings_one_row_only |
| QA-033 | pass | test_qa_033_device_row_seeded_once |
| QA-034 | pass | test_qa_034_second_device_row_refused |
| QA-035 | pass | test_qa_035_sse_without_url_refused |
| QA-036 | pass | test_qa_036_turn_role_check |
| QA-037 | pass | test_qa_037_turn_needs_content_or_tool_calls |
| QA-038 | pass | test_qa_038_negative_latency_refused |
| QA-039 | pass | test_qa_039_end_before_start_refused |
| QA-040 | pass | test_qa_040_delete_cascades_to_turns |
| QA-041 | pass | test_qa_041_settings_range_checks (3 params) |
| QA-042 | partial | test_qa_042 (pass) checks the jsonb object rule and that a valid value is accepted; the MCP_SECRET_ prefix rejection is not automated: the database does not enforce it, it belongs to the server (#35) |
| QA-043 | pass | test_qa_043_tool_name_unique_per_server (the case is tool uniqueness; the OPEN prefix note belongs to QA-042) |
| QA-044 | pass | test_qa_044_new_tool_defaults |
| QA-045 | pass | test_qa_045_turn_id_is_generated |
| QA-046 | pass | test_qa_046_migrate_creates_no_role |
| QA-047 | pass | checked by hand: `docker compose config` shows depends_on migrate, service_completed_successfully |
| QA-048 | pass | checked by hand: `config --services` lists postgres-test only with `--profile test` |
| QA-049 | not-automated | OPEN: result is undefined; docs say migrate connects directly |
| QA-050 | partial | test_qa_050_upgrade_is_fast (pass) asserts under 30 s; the bound is a QA proposal |

Note on QA-004: failure frees the lock is covered (E-pg::test_failed_migration_leaves_nothing); only the server-container part is not automated.

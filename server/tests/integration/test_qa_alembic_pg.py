"""QA cases for alembic-migrations that need a real Postgres. The case id is in each test name."""

import logging
import time
from collections.abc import Callable

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from ota_server.db import check as check_module
from ota_server.db.check import RevisionMismatchError, check_revision
from ota_server.db.migrate import LOCK_APP_KEY, MigrationError, migrate
from ota_server.db.names import schema_name
from ota_server.db.settings import DbSettings

SECRET_URL_PW = "s3cretpw"


def q(settings: DbSettings) -> str:
    return f'"{schema_name(settings.device_id)}"'


def run(engine: Engine, sql: str, params: dict[str, object] | None = None) -> object:
    with engine.begin() as conn:
        result = conn.execute(text(sql), params or {})
        return result.scalar() if result.returns_rows else None


def rejected(engine: Engine, sql: str, params: dict[str, object] | None = None) -> None:
    with pytest.raises((IntegrityError, DBAPIError)), engine.connect() as conn:
        conn.execute(text(sql), params or {})


def new_conversation(engine: Engine, s: str) -> object:
    return run(engine, f"INSERT INTO {s}.conversation DEFAULT VALUES RETURNING id")


def test_qa_006_no_url_in_logs_on_success(
    settings: DbSettings, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    migrate(settings)
    secret = settings.database_url.get_secret_value()  # type: ignore[union-attr]
    assert secret not in caplog.text


def test_qa_007_unreachable_has_no_password(
    device_id: str, caplog: pytest.LogCaptureFixture
) -> None:
    bad = DbSettings(
        device_id=device_id,
        database_url=f"postgresql+psycopg://ota:{SECRET_URL_PW}@127.0.0.1:1/ota",  # type: ignore[arg-type]
    )
    caplog.set_level(logging.DEBUG)
    with pytest.raises(MigrationError) as info:
        migrate(bad)
    assert SECRET_URL_PW not in caplog.text
    assert SECRET_URL_PW not in str(info.value)


def test_qa_009_second_run_keeps_seed_rows(settings: DbSettings, admin_engine: Engine) -> None:
    s = q(settings)
    migrate(settings)
    first = run(admin_engine, f"SELECT version_num FROM {s}.alembic_version")
    migrate(settings)
    assert run(admin_engine, f"SELECT version_num FROM {s}.alembic_version") == first
    assert run(admin_engine, f"SELECT count(*) FROM {s}.alembic_version") == 1
    assert run(admin_engine, f"SELECT count(*) FROM {s}.settings") == 1
    assert run(admin_engine, f"SELECT count(*) FROM {s}.mcp_server WHERE name='device'") == 1


def test_qa_010_two_instances_share_one_database(
    admin_url: str, admin_engine: Engine, device_id: str
) -> None:
    alpha = DbSettings(device_id=device_id + "a", database_url=admin_url)  # type: ignore[arg-type]
    beta = DbSettings(device_id=device_id + "b", database_url=admin_url)  # type: ignore[arg-type]
    holder = admin_engine.connect()
    holder.execute(
        text("SELECT pg_advisory_lock(:app, hashtext(:s))"),
        {"app": LOCK_APP_KEY, "s": schema_name(alpha.device_id)},
    )
    try:
        started = time.monotonic()
        migrate(beta)
        assert time.monotonic() - started < 10
    finally:
        holder.close()
    migrate(alpha)
    try:
        for st in (alpha, beta):
            assert run(admin_engine, f"SELECT count(*) FROM {q(st)}.alembic_version") == 1
    finally:
        for st in (alpha, beta):
            run(admin_engine, f"DROP SCHEMA IF EXISTS {q(st)} CASCADE")


def test_qa_012_empty_device_id_creates_no_schema(admin_url: str, admin_engine: Engine) -> None:
    with pytest.raises(ValueError, match="pattern"):
        DbSettings(device_id="", database_url=admin_url)  # type: ignore[arg-type]
    assert run(admin_engine, "SELECT count(*) FROM pg_namespace WHERE nspname = 'ota_'") == 0


def test_qa_016_quote_device_id_rejected_before_connection(admin_engine: Engine) -> None:
    with pytest.raises(ValueError):
        DbSettings(
            device_id='a"; DROP SCHEMA public; --',
            database_url="postgresql+psycopg://x:y@127.0.0.1:1/z",  # type: ignore[arg-type]
        )
    assert run(admin_engine, "SELECT count(*) FROM pg_namespace WHERE nspname='public'") == 1


def test_qa_018_schema_is_created(settings: DbSettings, admin_engine: Engine) -> None:
    assert (
        run(admin_engine, "SELECT to_regnamespace(:n)", {"n": schema_name(settings.device_id)})
        is None
    )
    migrate(settings)
    s = q(settings)
    assert run(admin_engine, f"SELECT count(*) FROM {s}.settings") == 1


def test_qa_019_existing_schema_needs_no_create_privilege(
    settings: DbSettings,
    admin_engine: Engine,
    role_url: Callable[[str, str], str],
) -> None:
    role, pw = "r19" + settings.device_id[:10], "pw19" + settings.device_id[:10]
    s = q(settings)
    run(admin_engine, f"CREATE ROLE {role} LOGIN PASSWORD '{pw}'")
    try:
        run(admin_engine, f"CREATE SCHEMA {s} AUTHORIZATION {role}")
        migrate(DbSettings(device_id=settings.device_id, database_url=role_url(role, pw)))  # type: ignore[arg-type]
        assert run(admin_engine, f"SELECT count(*) FROM {s}.settings") == 1
    finally:
        run(admin_engine, f"DROP SCHEMA IF EXISTS {s} CASCADE")
        run(admin_engine, f"DROP OWNED BY {role} CASCADE")
        run(admin_engine, f"DROP ROLE {role}")


def test_qa_020_version_table_in_instance_schema(
    settings: DbSettings, admin_engine: Engine
) -> None:
    migrate(settings)
    schema = schema_name(settings.device_id)
    in_schema = (
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_name='alembic_version' AND table_schema=:s"
    )
    assert run(admin_engine, in_schema, {"s": schema}) == 1
    # public may hold the table from another test run of an older version; this run adds none
    assert run(admin_engine, in_schema, {"s": "public"}) == 0


def test_qa_021_killed_holder_frees_the_lock(settings: DbSettings, admin_engine: Engine) -> None:
    schema = schema_name(settings.device_id)
    holder = admin_engine.connect()
    holder.execute(
        text("SELECT pg_advisory_lock(:app, hashtext(:s))"), {"app": LOCK_APP_KEY, "s": schema}
    )
    pid = holder.execute(text("SELECT pg_backend_pid()")).scalar()
    run(admin_engine, "SELECT pg_terminate_backend(:p)", {"p": pid})
    started = time.monotonic()
    migrate(settings)
    assert time.monotonic() - started < 30
    assert run(admin_engine, f"SELECT count(*) FROM {q(settings)}.alembic_version") == 1


def test_qa_022_unreachable_host_exits_fast(device_id: str) -> None:
    bad = DbSettings(
        device_id=device_id,
        database_url="postgresql+psycopg://u:p@127.0.0.1:1/z",  # type: ignore[arg-type]
    )
    started = time.monotonic()
    with pytest.raises(MigrationError):
        migrate(bad)
    assert time.monotonic() - started < 30


def test_qa_024_real_revision_mismatch(
    settings: DbSettings, admin_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    migrate(settings)
    run(admin_engine, f"UPDATE {q(settings)}.alembic_version SET version_num = '0000_old'")
    caplog.set_level(logging.DEBUG)
    with pytest.raises(RevisionMismatchError):
        check_revision(settings)
    head = check_module.code_head()
    assert head is not None
    assert "0000_old" in caplog.text and head in caplog.text
    assert settings.database_url.get_secret_value() not in caplog.text  # type: ignore[union-attr]


def test_qa_025_real_missing_schema(settings: DbSettings, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    with pytest.raises(RevisionMismatchError, match="differs"):
        check_revision(settings)
    assert "none" in caplog.text


def test_qa_026_real_missing_version_table(
    settings: DbSettings, admin_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    run(admin_engine, f"CREATE SCHEMA {q(settings)}")
    caplog.set_level(logging.DEBUG)
    with pytest.raises(RevisionMismatchError, match="differs"):
        check_revision(settings)
    assert "none" in caplog.text


def test_qa_027_real_revision_at_head(settings: DbSettings) -> None:
    migrate(settings)
    check_revision(settings)


def test_qa_029_030_downgrade_flag(settings: DbSettings, admin_engine: Engine) -> None:
    migrate(settings)
    s = q(settings)
    rev = run(admin_engine, f"SELECT version_num FROM {s}.alembic_version")
    with pytest.raises(MigrationError):
        migrate(settings, downgrade=True)
    assert run(admin_engine, f"SELECT version_num FROM {s}.alembic_version") == rev
    assert run(admin_engine, f"SELECT count(*) FROM {s}.settings") == 1
    migrate(settings, downgrade=True, allow_downgrade=True)
    assert (
        run(
            admin_engine, "SELECT to_regclass(:n)", {"n": f"{schema_name(settings.device_id)}.turn"}
        )
        is None
    )


def test_qa_031_settings_seed(settings: DbSettings, admin_engine: Engine) -> None:
    migrate(settings)
    with admin_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT language, voice, system_prompt, volume, retention_days, "
                f"memory_retention_days, speech_cap_s FROM {q(settings)}.settings"
            )
        ).all()
    assert [tuple(r) for r in rows] == [("en", "", "", 50, 90, 365, 60)]


def test_qa_032_settings_one_row_only(settings: DbSettings, admin_engine: Engine) -> None:
    migrate(settings)
    rejected(admin_engine, f"INSERT INTO {q(settings)}.settings (id) VALUES (false)")


def test_qa_033_device_row_seeded_once(settings: DbSettings, admin_engine: Engine) -> None:
    migrate(settings)
    migrate(settings)
    with admin_engine.connect() as conn:
        rows = conn.execute(
            text(f"SELECT name, transport, url FROM {q(settings)}.mcp_server")
        ).all()
    assert [tuple(r) for r in rows] == [("device", "device", None)]


def test_qa_034_second_device_row_refused(settings: DbSettings, admin_engine: Engine) -> None:
    migrate(settings)
    rejected(
        admin_engine,
        f"INSERT INTO {q(settings)}.mcp_server (name, transport) VALUES ('device2', 'device')",
    )


def test_qa_035_sse_without_url_refused(settings: DbSettings, admin_engine: Engine) -> None:
    migrate(settings)
    rejected(
        admin_engine,
        f"INSERT INTO {q(settings)}.mcp_server (name, transport) VALUES ('x', 'sse')",
    )


def test_qa_036_turn_role_check(settings: DbSettings, admin_engine: Engine) -> None:
    migrate(settings)
    c = new_conversation(admin_engine, q(settings))
    rejected(
        admin_engine,
        f"INSERT INTO {q(settings)}.turn (conversation_id, role, content) "
        "VALUES (:c, 'system', 'x')",
        {"c": c},
    )


def test_qa_037_turn_needs_content_or_tool_calls(
    settings: DbSettings, admin_engine: Engine
) -> None:
    migrate(settings)
    c = new_conversation(admin_engine, q(settings))
    rejected(
        admin_engine,
        f"INSERT INTO {q(settings)}.turn (conversation_id, role) VALUES (:c, 'user')",
        {"c": c},
    )


def test_qa_038_negative_latency_refused(settings: DbSettings, admin_engine: Engine) -> None:
    migrate(settings)
    c = new_conversation(admin_engine, q(settings))
    rejected(
        admin_engine,
        f"INSERT INTO {q(settings)}.turn (conversation_id, role, content, latency_ms) "
        "VALUES (:c, 'user', 'x', -1)",
        {"c": c},
    )


def test_qa_039_end_before_start_refused(settings: DbSettings, admin_engine: Engine) -> None:
    migrate(settings)
    rejected(
        admin_engine,
        f"INSERT INTO {q(settings)}.conversation (started_at, ended_at) "
        "VALUES ('2026-11-27 10:00+00', '2026-11-27 09:00+00')",
    )


def test_qa_040_delete_cascades_to_turns(settings: DbSettings, admin_engine: Engine) -> None:
    migrate(settings)
    s = q(settings)
    c = new_conversation(admin_engine, s)
    for _ in range(3):
        run(
            admin_engine,
            f"INSERT INTO {s}.turn (conversation_id, role, content) VALUES (:c, 'user', 'x')",
            {"c": c},
        )
    assert (
        run(admin_engine, f"SELECT count(*) FROM {s}.turn WHERE conversation_id=:c", {"c": c}) == 3
    )
    run(admin_engine, f"DELETE FROM {s}.conversation WHERE id=:c", {"c": c})
    assert (
        run(admin_engine, f"SELECT count(*) FROM {s}.turn WHERE conversation_id=:c", {"c": c}) == 0
    )


@pytest.mark.parametrize("assignment", ["volume = 101", "language = 'de'", "speech_cap_s = 4"])
def test_qa_041_settings_range_checks(
    settings: DbSettings, admin_engine: Engine, assignment: str
) -> None:
    migrate(settings)
    rejected(admin_engine, f"UPDATE {q(settings)}.settings SET {assignment}")


def test_qa_042_secret_headers_must_be_an_object(
    settings: DbSettings, admin_engine: Engine
) -> None:
    """Only the jsonb object rule is a database rule. The MCP_SECRET_ prefix is for #35."""
    migrate(settings)
    s = q(settings)
    rejected(
        admin_engine,
        f"INSERT INTO {s}.mcp_server (name, transport, url, secret_headers) "
        "VALUES ('a', 'sse', 'http://x', '[\"x\"]'::jsonb)",
    )
    run(
        admin_engine,
        f"INSERT INTO {s}.mcp_server (name, transport, url, secret_headers) "
        "VALUES ('b', 'sse', 'http://x', '{\"Authorization\": \"MCP_SECRET_N8N\"}'::jsonb) "
        "RETURNING id",
    )


def test_qa_043_tool_name_unique_per_server(settings: DbSettings, admin_engine: Engine) -> None:
    migrate(settings)
    s = q(settings)
    server = run(admin_engine, f"SELECT id FROM {s}.mcp_server WHERE name='device'")
    insert = f"INSERT INTO {s}.tool (mcp_server_id, name, schema_hash) VALUES (:m, 'search', 'h')"
    run(admin_engine, insert + " RETURNING id", {"m": server})
    rejected(admin_engine, insert, {"m": server})


def test_qa_044_new_tool_defaults(settings: DbSettings, admin_engine: Engine) -> None:
    migrate(settings)
    s = q(settings)
    server = run(admin_engine, f"SELECT id FROM {s}.mcp_server WHERE name='device'")
    with admin_engine.connect() as conn:
        row = conn.execute(
            text(
                f"INSERT INTO {s}.tool (mcp_server_id, name, schema_hash) VALUES (:m, 't', 'h') "
                "RETURNING enabled, confirm"
            ),
            {"m": server},
        ).one()
    assert tuple(row) == (False, True)


def test_qa_045_turn_id_is_generated(settings: DbSettings, admin_engine: Engine) -> None:
    migrate(settings)
    s = q(settings)
    c = new_conversation(admin_engine, s)
    rejected(
        admin_engine,
        f"INSERT INTO {s}.turn (id, conversation_id, role, content) VALUES (5, :c, 'user', 'x')",
        {"c": c},
    )


def test_qa_046_migrate_creates_no_role(settings: DbSettings, admin_engine: Engine) -> None:
    migrate(settings)
    assert run(admin_engine, "SELECT count(*) FROM pg_roles WHERE rolname LIKE 'ota\\_%'") == 0


def test_qa_050_upgrade_is_fast(settings: DbSettings) -> None:
    started = time.monotonic()
    migrate(settings)
    assert time.monotonic() - started < 30

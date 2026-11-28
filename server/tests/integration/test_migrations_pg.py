"""0001_initial against a real Postgres 16: up/down/up, seeds, constraints, locking, failure."""

import shutil
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from ota_server.db import migrate as migrate_module
from ota_server.db.migrate import LOCK_APP_KEY, MigrationError, migrate
from ota_server.db.names import schema_name
from ota_server.db.settings import DbSettings

TABLES = {"conversation", "turn", "settings", "mcp_server", "tool"}
LONG_WAIT_SECONDS = 11.0


def table_names(engine: Engine, device_id: str) -> set[str]:
    query = text("SELECT table_name FROM information_schema.tables WHERE table_schema = :s")
    with engine.connect() as conn:
        rows = conn.execute(query, {"s": schema_name(device_id)}).all()
    return {r[0] for r in rows} - {"alembic_version"}


def version_rows(engine: Engine, device_id: str) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(f'SELECT version_num FROM "{schema_name(device_id)}".alembic_version')
        )
        return [r[0] for r in rows]


def down(settings: DbSettings) -> None:
    migrate(settings, downgrade=True, allow_downgrade=True)


def test_upgrade_downgrade_upgrade(settings: DbSettings, admin_engine: Engine) -> None:
    migrate(settings)
    assert table_names(admin_engine, settings.device_id) == TABLES
    down(settings)
    assert table_names(admin_engine, settings.device_id) == set()
    migrate(settings)
    assert table_names(admin_engine, settings.device_id) == TABLES


def test_nothing_lands_in_public(settings: DbSettings, admin_engine: Engine) -> None:
    migrate(settings)
    query = text(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' "
        "AND table_name = ANY(:names)"
    )
    with admin_engine.connect() as conn:
        assert conn.execute(query, {"names": sorted(TABLES)}).scalar() == 0


def test_second_run_changes_nothing(settings: DbSettings, admin_engine: Engine) -> None:
    migrate(settings)
    first = version_rows(admin_engine, settings.device_id)
    migrate(settings)
    assert len(first) == 1
    assert version_rows(admin_engine, settings.device_id) == first


def test_seeds(settings: DbSettings, admin_engine: Engine) -> None:
    migrate(settings)
    schema = schema_name(settings.device_id)
    with admin_engine.connect() as conn:
        rows = conn.execute(
            text(f'SELECT language, voice, system_prompt, volume FROM "{schema}".settings')
        ).all()
        servers = conn.execute(
            text(f'SELECT name, transport, url FROM "{schema}".mcp_server')
        ).all()
    assert [tuple(r) for r in rows] == [("en", "", "", 50)]
    assert [tuple(r) for r in servers] == [("device", "device", None)]


def test_constraints_hold(settings: DbSettings, admin_engine: Engine) -> None:
    migrate(settings)
    schema = schema_name(settings.device_id)
    with admin_engine.connect() as conn:
        with pytest.raises(IntegrityError, match="unique|duplicate") as dup:
            conn.execute(
                text(
                    f"INSERT INTO \"{schema}\".mcp_server (name, transport) VALUES ('d2','device')"
                )
            )
        assert dup.value.orig is not None
        assert getattr(dup.value.orig, "sqlstate", None) == "23505"
    with admin_engine.connect() as conn:
        conv = conn.execute(
            text(f'INSERT INTO "{schema}".conversation DEFAULT VALUES RETURNING id')
        ).scalar()
        with pytest.raises(IntegrityError) as chk:
            conn.execute(
                text(
                    f'INSERT INTO "{schema}".turn (conversation_id, role, content) '
                    "VALUES (:c, 'x', 'hi')"
                ),
                {"c": conv},
            )
        assert chk.value.orig is not None
        assert getattr(chk.value.orig, "sqlstate", None) == "23514"


def test_constraints_are_named(settings: DbSettings, admin_engine: Engine) -> None:
    migrate(settings)
    query = text(
        "SELECT conname FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
        "WHERE n.nspname = :s"
    )
    with admin_engine.connect() as conn:
        names = [r[0] for r in conn.execute(query, {"s": schema_name(settings.device_id)})]
    assert names
    assert not [n for n in names if "_check" in n and not n.startswith("ck_")]
    assert all(n.split("_")[0] in {"pk", "ck", "fk", "uq", "alembic"} for n in names)


@contextmanager
def barrier_threads(targets: list[Any]) -> Iterator[list[BaseException]]:
    errors: list[BaseException] = []
    start = threading.Barrier(len(targets))

    def wrap(fn: Any) -> None:
        try:
            start.wait()
            fn()
        except BaseException as exc:  # noqa: BLE001 - collected and asserted by the test
            errors.append(exc)

    threads = [threading.Thread(target=wrap, args=(t,)) for t in targets]
    for t in threads:
        t.start()
    yield errors
    for t in threads:
        t.join(timeout=120)


def test_two_parallel_runners(settings: DbSettings, admin_engine: Engine) -> None:
    with barrier_threads([lambda: migrate(settings), lambda: migrate(settings)]) as errors:
        pass
    assert errors == []
    assert len(version_rows(admin_engine, settings.device_id)) == 1
    assert table_names(admin_engine, settings.device_id) == TABLES


@pytest.fixture
def short_timeout_role(admin_engine: Engine, admin_url: str) -> Iterator[tuple[str, str]]:
    role = "r" + uuid.uuid4().hex[:12]
    password = uuid.uuid4().hex
    with admin_engine.connect() as conn:
        conn.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'"))
        conn.execute(text(f"ALTER ROLE {role} SET statement_timeout = '10s'"))
        conn.execute(text(f"GRANT CREATE ON DATABASE {conn.engine.url.database} TO {role}"))
    yield role, password
    with admin_engine.connect() as conn:
        conn.execute(text(f"DROP OWNED BY {role} CASCADE"))
        conn.execute(text(f"DROP ROLE {role}"))


@pytest.mark.slow
def test_long_wait_is_not_cancelled(
    settings: DbSettings,
    admin_engine: Engine,
    admin_url: str,
    short_timeout_role: tuple[str, str],
    role_url: Callable[[str, str], str],
) -> None:
    role, password = short_timeout_role
    role_settings = DbSettings(
        device_id=settings.device_id,
        database_url=role_url(role, password),  # type: ignore[arg-type]
    )
    schema = schema_name(settings.device_id)
    holder = admin_engine.connect()
    holder.execute(
        text("SELECT pg_advisory_lock(:app, hashtext(:s))"), {"app": LOCK_APP_KEY, "s": schema}
    )
    errors: list[BaseException] = []

    def second() -> None:
        try:
            migrate(role_settings)
        except BaseException as exc:  # noqa: BLE001 - asserted below
            errors.append(exc)

    thread = threading.Thread(target=second)
    thread.start()
    time.sleep(LONG_WAIT_SECONDS)
    assert thread.is_alive()
    holder.execute(
        text("SELECT pg_advisory_unlock(:app, hashtext(:s))"), {"app": LOCK_APP_KEY, "s": schema}
    )
    holder.close()
    thread.join(timeout=60)
    assert errors == []
    assert table_names(admin_engine, settings.device_id) == TABLES


def failing_scripts(target: Path) -> Path:
    source = Path(migrate_module.PACKAGE_DIR) / "migrations"
    shutil.copy(source / "env.py", target / "env.py")
    shutil.copy(source / "script.py.mako", target / "script.py.mako")
    versions = target / "versions"
    versions.mkdir()
    (versions / "9001_fail.py").write_text(
        'revision = "9001"\ndown_revision = None\nbranch_labels = None\ndepends_on = None\n\n\n'
        "from alembic import op\n\n\ndef upgrade() -> None:\n"
        '    op.execute("CREATE TABLE half_done (id int)")\n'
        '    raise RuntimeError("boom")\n\n\ndef downgrade() -> None:\n    pass\n'
    )
    return target


def test_failed_migration_leaves_nothing(
    settings: DbSettings, admin_engine: Engine, tmp_path: Path
) -> None:
    scripts = failing_scripts(tmp_path)

    def upgrade_fn(config: Config, revision: str) -> None:
        config.set_main_option("script_location", str(scripts))
        command.upgrade(config, revision)

    with pytest.raises(RuntimeError, match="boom"):
        migrate(settings, upgrade_fn=upgrade_fn)
    schema = schema_name(settings.device_id)
    with admin_engine.connect() as conn:
        found = conn.execute(
            text(
                "SELECT count(*) FROM information_schema.tables WHERE table_name = 'half_done' "
                "AND table_schema IN (:s, 'public')"
            ),
            {"s": schema},
        ).scalar()
        free = conn.execute(
            text("SELECT pg_try_advisory_lock(:app, hashtext(:s))"),
            {"app": LOCK_APP_KEY, "s": schema},
        ).scalar()
        conn.execute(
            text("SELECT pg_advisory_unlock(:app, hashtext(:s))"),
            {"app": LOCK_APP_KEY, "s": schema},
        )
    assert found == 0
    assert free is True
    assert table_names(admin_engine, settings.device_id) == set()


def test_lock_wait_gives_up(settings: DbSettings, admin_engine: Engine) -> None:
    schema = schema_name(settings.device_id)
    with admin_engine.connect() as holder:
        holder.execute(
            text("SELECT pg_advisory_lock(:app, hashtext(:s))"), {"app": LOCK_APP_KEY, "s": schema}
        )
        with pytest.raises(MigrationError):
            migrate(settings, lock_wait_seconds=0.5, poll_seconds=0.1)
        holder.execute(
            text("SELECT pg_advisory_unlock(:app, hashtext(:s))"),
            {"app": LOCK_APP_KEY, "s": schema},
        )

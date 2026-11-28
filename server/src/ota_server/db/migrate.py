"""migrate(settings): take the advisory lock, create the schema, run Alembic, release the lock.

Never logs the database URL.
"""

import logging
import time
from collections.abc import Callable

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, text

from ota_server.db.engine import base_config
from ota_server.db.engine import connect as _connect
from ota_server.db.names import quoted_schema_name, schema_name
from ota_server.db.settings import DbSettings

LOCK_APP_KEY = 0x4F544149  # ASCII "OTAI"
LOCK_WAIT_SECONDS = 300.0
LOCK_POLL_SECONDS = 1.0
DDL_LOCK_TIMEOUT = "30s"
RUN_STATEMENT_TIMEOUT = "30min"
RUN_IDLE_IN_TRANSACTION_TIMEOUT = "10min"

log = logging.getLogger(__name__)

Connect = Callable[[DbSettings], Connection]
Runner = Callable[[Config, str], None]


class MigrationError(RuntimeError):
    """A migration could not run. The message never holds the database URL."""


def _upgrade(config: Config, revision: str) -> None:
    command.upgrade(config, revision)


def _downgrade(config: Config, revision: str) -> None:
    command.downgrade(config, revision)


def _config(connection: Connection, schema: str) -> Config:
    config = base_config()
    config.set_main_option("version_table_schema", schema)
    config.attributes["connection"] = connection
    return config


def _acquire_lock(connection: Connection, schema: str, wait: float, poll: float) -> None:
    query = text("SELECT pg_try_advisory_lock(:app, hashtext(:schema))")
    deadline = time.monotonic() + wait
    while True:
        if connection.execute(query, {"app": LOCK_APP_KEY, "schema": schema}).scalar():
            return
        if time.monotonic() >= deadline:
            log.error("migration lock wait exceeded %.0f s for schema %s", wait, schema)
            raise MigrationError("timed out waiting for the migration lock")
        time.sleep(poll)


def _ensure_schema(connection: Connection, device_id: str, schema: str) -> None:
    found = connection.execute(text("SELECT to_regnamespace(:schema)"), {"schema": schema})
    if found.scalar() is None:
        identifier = quoted_schema_name(device_id).as_string()
        connection.execute(text(f"CREATE SCHEMA {identifier}"))
    connection.commit()


def _release(connection: Connection, schema: str) -> None:
    """Roll back and unlock. A failure here is logged (class name only), never raised."""
    try:
        connection.rollback()
        connection.execute(
            text("SELECT pg_advisory_unlock(:app, hashtext(:schema))"),
            {"app": LOCK_APP_KEY, "schema": schema},
        )
    except Exception as exc:
        log.error("cannot release the migration lock (%s)", type(exc).__name__)


def _run_locked(
    connection: Connection,
    settings: DbSettings,
    run: Callable[[Config], None],
    waits: tuple[float, float],
) -> None:
    schema = schema_name(settings.device_id)
    _acquire_lock(connection, schema, *waits)
    try:
        connection.commit()
        # Finite backstops for the run itself; the try-lock polling needs no override.
        connection.execute(text(f"SET statement_timeout = '{RUN_STATEMENT_TIMEOUT}'"))
        connection.execute(
            text(f"SET idle_in_transaction_session_timeout = '{RUN_IDLE_IN_TRANSACTION_TIMEOUT}'")
        )
        _ensure_schema(connection, settings.device_id, schema)
        connection.execute(text(f"SET lock_timeout = '{DDL_LOCK_TIMEOUT}'"))
        # Migrations hold no schema name: unqualified DDL lands in the device schema.
        identifier = quoted_schema_name(settings.device_id).as_string()
        connection.execute(text(f"SET search_path TO {identifier}"))
        connection.commit()
        run(_config(connection, schema))
    finally:
        _release(connection, schema)


def migrate(
    settings: DbSettings,
    *,
    downgrade: bool = False,
    allow_downgrade: bool = False,
    downgrade_target: str = "-1",
    lock_wait_seconds: float = LOCK_WAIT_SECONDS,
    poll_seconds: float = LOCK_POLL_SECONDS,
    connect: Connect = _connect,
    upgrade_fn: Runner = _upgrade,
    downgrade_fn: Runner = _downgrade,
) -> None:
    """Bring the device schema to head (or down to downgrade_target with allow_downgrade)."""
    if downgrade and not allow_downgrade:
        raise MigrationError("downgrade needs allow_downgrade=True")
    try:
        connection = connect(settings)
    except Exception as exc:
        log.error("cannot connect to the database (%s)", type(exc).__name__)
        raise MigrationError("cannot connect to the database") from None
    revision = downgrade_target if downgrade else "head"
    runner = downgrade_fn if downgrade else upgrade_fn
    try:
        _run_locked(
            connection,
            settings,
            lambda config: runner(config, revision),
            (lock_wait_seconds, poll_seconds),
        )
    finally:
        connection.close()

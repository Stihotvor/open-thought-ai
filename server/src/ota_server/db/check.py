"""check_revision(settings): refuse to start when the database revision is not the code head.

A different revision, a missing schema and a missing alembic_version table all count as a
mismatch (database revision "none"). When the code has no revision yet (head None), a database
with no revision matches; any database revision then is a mismatch. Never logs the URL.
"""

import logging
from collections.abc import Callable

from alembic.script import ScriptDirectory
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Connection, text

from ota_server.config import ENV_FILE
from ota_server.db.engine import base_config
from ota_server.db.engine import connect as _connect
from ota_server.db.names import quoted_schema_name
from ota_server.db.settings import DbSettings

log = logging.getLogger(__name__)

Connect = Callable[[DbSettings], Connection]


class RevisionMismatchError(RuntimeError):
    """The database is not at the code head. The message never holds the database URL."""


class _UrlOnly(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OTA_", env_file=ENV_FILE, extra="ignore")
    database_url: SecretStr | None = None


def code_head() -> str | None:
    """The newest revision in the migrations directory, or None when there is none."""
    return ScriptDirectory.from_config(base_config()).get_current_head()


def _database_revision(connection: Connection, device_id: str) -> str | None:
    connection.execute(text("SET TRANSACTION READ ONLY"))
    table = f"{quoted_schema_name(device_id).as_string()}.alembic_version"
    found = connection.execute(text("SELECT to_regclass(:name)"), {"name": table})
    if found.scalar() is None:
        return None  # schema or table missing
    query = text(f"SELECT version_num FROM {table}")  # nosec B608 - quoted, validated identifier
    row = connection.execute(query)
    value = row.scalar()
    return None if value is None else str(value)


def _read_revision(settings: DbSettings, connect: Connect) -> str | None:
    try:
        connection = connect(settings)
    except Exception as exc:
        log.error("cannot connect to the database (%s)", type(exc).__name__)
        raise RevisionMismatchError("cannot connect to the database") from None
    try:
        return _database_revision(connection, settings.device_id)
    except Exception as exc:
        log.error("cannot read the database revision (%s)", type(exc).__name__)
        raise RevisionMismatchError("cannot read the database revision") from None
    finally:
        connection.rollback()
        connection.close()


def check_revision(
    settings: DbSettings,
    *,
    connect: Connect = _connect,
    head_fn: Callable[[], str | None] = code_head,
) -> None:
    """Raise RevisionMismatchError unless the database revision equals the code head."""
    if settings.database_url is None:
        return
    expected = head_fn()
    actual = _read_revision(settings, connect)
    if actual != expected:
        log.error("database revision %s, code head %s: run ota-migrate", actual or "none", expected)
        raise RevisionMismatchError(
            f"database revision {actual or 'none'} differs from code head {expected}"
        )


def check_startup() -> None:
    """Lifespan hook: check only when OTA_DATABASE_URL is set (rule 9), else do nothing."""
    if _UrlOnly().database_url is None:
        return
    check_revision(DbSettings())  # type: ignore[call-arg]

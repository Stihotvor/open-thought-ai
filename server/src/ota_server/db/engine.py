"""Engine for migrations: one direct connection, no pool. The URL is never logged."""

from pathlib import Path

from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine
from sqlalchemy.pool import NullPool

from ota_server.db.settings import DbSettings

PACKAGE_DIR = Path(__file__).resolve().parent
CONNECT_TIMEOUT_SECONDS = 5


def make_engine(settings: DbSettings) -> Engine:
    """Build an engine with NullPool. Raises ValueError when no database URL is set."""
    if settings.database_url is None:
        raise ValueError("OTA_DATABASE_URL is not set")
    return create_engine(
        settings.database_url.get_secret_value(),
        poolclass=NullPool,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
    )


def connect(settings: DbSettings) -> Connection:
    """Open one direct connection."""
    return make_engine(settings).connect()


def base_config() -> Config:
    """Alembic config with the packaged ini file and migrations directory."""
    config = Config(str(PACKAGE_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(PACKAGE_DIR / "migrations"))
    return config

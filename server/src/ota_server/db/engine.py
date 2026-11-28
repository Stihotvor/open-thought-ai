"""Engine for migrations: one direct connection, no pool. The URL is never logged."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import NullPool

from ota_server.db.settings import DbSettings


def make_engine(settings: DbSettings) -> Engine:
    """Build an engine with NullPool. Raises ValueError when no database URL is set."""
    if settings.database_url is None:
        raise ValueError("OTA_DATABASE_URL is not set")
    return create_engine(settings.database_url.get_secret_value(), poolclass=NullPool)

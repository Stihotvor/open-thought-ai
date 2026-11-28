"""Database code: schema names, settings, engine and migrations for one device."""

from ota_server.db.names import quoted_schema_name, schema_name
from ota_server.db.settings import DbSettings

__all__ = ["DbSettings", "quoted_schema_name", "schema_name"]

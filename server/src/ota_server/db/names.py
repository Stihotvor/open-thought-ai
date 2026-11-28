"""Schema names: one Postgres schema per device, named ota_<device_id>.

Models and migrations hold no schema name; they get it from here.
"""

import re

from psycopg import sql

DEVICE_ID_PATTERN = re.compile(r"[a-z0-9_]{1,40}")
SCHEMA_PREFIX = "ota_"


def schema_name(device_id: str) -> str:
    """The schema name for a device. Raises ValueError for an invalid DEVICE_ID."""
    if not DEVICE_ID_PATTERN.fullmatch(device_id):
        raise ValueError("DEVICE_ID must match ^[a-z0-9_]{1,40}$")
    return SCHEMA_PREFIX + device_id


def quoted_schema_name(device_id: str) -> sql.Identifier:
    """The schema name as a quoted SQL identifier."""
    return sql.Identifier(schema_name(device_id))

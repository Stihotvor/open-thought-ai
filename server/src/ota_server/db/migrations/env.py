"""Alembic environment. The connection and the version table schema come from migrate()."""

from typing import Any

from alembic import context
from sqlalchemy import Connection


def run_migrations(connection: Connection, version_schema: str) -> None:
    context.configure(
        connection=connection,
        target_metadata=None,
        version_table_schema=version_schema,
        transaction_per_migration=False,
    )
    with context.begin_transaction():
        context.run_migrations()


def main() -> None:
    config = context.config
    attributes: dict[str, Any] = config.attributes
    connection: Connection | None = attributes.get("connection")
    if connection is None:
        raise RuntimeError("migrate() must pass the connection in config.attributes")
    version_schema = config.get_main_option("version_table_schema")
    if not version_schema:
        raise RuntimeError("migrate() must pass version_table_schema")
    run_migrations(connection, version_schema)


main()

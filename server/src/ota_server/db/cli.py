"""ota-migrate: bring the device schema to head. Exit 0 on success, non-zero on any failure.

Downgrade needs both --downgrade and --allow-downgrade. Log lines never hold the database URL
(only the exception class name, except for MigrationError whose message is URL-free).
"""

import argparse
import logging
from collections.abc import Callable, Sequence
from typing import Any

from ota_server.db.migrate import MigrationError, migrate
from ota_server.db.settings import DbSettings

log = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ota-migrate", description=__doc__)
    parser.add_argument("--downgrade", action="store_true", help="downgrade to base")
    parser.add_argument(
        "--allow-downgrade", action="store_true", help="confirm that a downgrade is wanted"
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    migrate_fn: Callable[..., Any] = migrate,
    load: Callable[[], DbSettings] = lambda: DbSettings(),  # type: ignore[call-arg]
) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = _parser().parse_args(argv)
    if args.downgrade and not args.allow_downgrade:
        log.error("a downgrade needs --allow-downgrade")
        return 2
    try:
        settings = load()
    except Exception as exc:
        log.error("cannot load the database settings (%s)", type(exc).__name__)
        return 1
    try:
        migrate_fn(settings, downgrade=args.downgrade, allow_downgrade=args.allow_downgrade)
    except MigrationError as exc:
        log.error("migration failed: %s", exc)
        return 1
    except Exception as exc:
        log.error("migration failed (%s)", type(exc).__name__)
        return 1
    log.info("migration done")
    return 0

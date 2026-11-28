"""The migrate CLI: exit codes, the downgrade flag, no URL in logs. migrate() is faked."""

import logging
from typing import Any

import pytest

from ota_server.db.cli import main
from ota_server.db.migrate import MigrationError
from ota_server.db.settings import DbSettings

URL = "postgresql+psycopg://user:s3cretpw@db.example:5432/ota"


def load() -> DbSettings:
    return DbSettings(device_id="dev1", database_url=URL)  # type: ignore[arg-type]


class FakeMigrate:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def __call__(self, settings: DbSettings, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error


def test_cli_success() -> None:
    fake = FakeMigrate()
    assert main([], migrate_fn=fake, load=load) == 0
    assert len(fake.calls) == 1
    assert not fake.calls[0].get("downgrade")


@pytest.mark.parametrize(
    "error",
    [
        MigrationError("cannot connect to the database"),
        RuntimeError(f"boom {URL}"),
    ],
)
def test_cli_failure_exit_and_no_url(error: Exception, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    code = main([], migrate_fn=FakeMigrate(error), load=load)
    assert code != 0
    assert "s3cretpw" not in caplog.text
    assert URL not in caplog.text


def test_downgrade_needs_flag() -> None:
    fake = FakeMigrate()
    assert main(["--downgrade"], migrate_fn=fake, load=load) != 0
    assert fake.calls == []


def test_downgrade_with_flag_is_passed() -> None:
    fake = FakeMigrate()
    assert main(["--downgrade", "--allow-downgrade"], migrate_fn=fake, load=load) == 0
    assert fake.calls[0]["downgrade"] is True
    assert fake.calls[0]["allow_downgrade"] is True


def test_settings_failure_is_nonzero() -> None:
    def bad() -> DbSettings:
        raise ValueError("bad settings")

    assert main([], migrate_fn=FakeMigrate(), load=bad) != 0

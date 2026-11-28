"""Database settings (alembic-migrations T1): OTA_DATABASE_URL is read from env and hidden."""

from pathlib import Path

import pytest

from ota_server.db.settings import DbSettings

URL = "postgresql://u:secret@h/db"


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The device id, and an empty working directory so no env file is read."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OTA_DEVICE_ID", "test")
    monkeypatch.delenv("OTA_DATABASE_URL", raising=False)


def load() -> DbSettings:
    return DbSettings()  # type: ignore[call-arg]  # values come from the environment


def test_database_url_is_read_and_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTA_DATABASE_URL", URL)
    settings = load()
    assert settings.database_url is not None
    assert settings.database_url.get_secret_value() == URL
    assert "secret" not in repr(settings)
    assert "secret" not in str(settings)

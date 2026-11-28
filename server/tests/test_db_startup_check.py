"""Startup revision check: fake connection, no Postgres. Covers check_revision and the lifespan."""

import hashlib
import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from ota_server import main as main_module
from ota_server.config import get_settings
from ota_server.db.check import RevisionMismatchError, check_revision
from ota_server.db.settings import DbSettings

URL = "postgresql+psycopg://user:s3cretpw@db.example:5432/ota"
HEAD = "abc123"


class FakeResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar(self) -> object:
        return self._value


class FakeConnection:
    """state: 'ok', 'differs', 'no_schema', 'no_table', 'empty'."""

    def __init__(self, state: str) -> None:
        self.state = state
        self.sql: list[str] = []
        self.closed = False

    def execute(self, statement: Any, params: Any = None) -> FakeResult:
        text = str(statement)
        self.sql.append(text)
        if "to_regclass" in text:
            return FakeResult(None if self.state in ("no_schema", "no_table") else "t")
        if "version_num" in text:
            return FakeResult({"ok": HEAD, "differs": "zzz999", "empty": None}[self.state])
        return FakeResult(None)

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def app_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTA_DEVICE_ID", "test")
    monkeypatch.setenv("OTA_DEVICE_TOKEN_SHA256", hashlib.sha256(b"t").hexdigest())
    monkeypatch.setenv("OTA_MCP_SERVERS_FILE", "")
    get_settings.cache_clear()


def settings() -> DbSettings:
    return DbSettings(device_id="dev1", database_url=SecretStr(URL))


@pytest.mark.parametrize("state", ["differs", "no_schema", "no_table"])
def test_start_refused(state: str, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    conn = FakeConnection(state)
    with pytest.raises(RevisionMismatchError) as info:
        check_revision(settings(), connect=lambda s: conn, head_fn=lambda: HEAD)  # type: ignore[arg-type,return-value]
    assert conn.closed
    expected = "zzz999" if state == "differs" else "none"
    assert expected in caplog.text and HEAD in caplog.text
    assert "s3cretpw" not in caplog.text and URL not in caplog.text
    assert "s3cretpw" not in str(info.value)
    assert any("READ ONLY" in s for s in conn.sql)


def test_revision_matches() -> None:
    check_revision(settings(), connect=lambda s: FakeConnection("ok"), head_fn=lambda: HEAD)  # type: ignore[arg-type,return-value]


def test_empty_table_with_no_head_matches() -> None:
    check_revision(settings(), connect=lambda s: FakeConnection("empty"), head_fn=lambda: None)  # type: ignore[arg-type,return-value]


def test_connect_failure_has_no_url(caplog: pytest.LogCaptureFixture) -> None:
    def boom(s: DbSettings) -> Any:
        raise OSError(URL)

    with pytest.raises(RevisionMismatchError) as info:
        check_revision(settings(), connect=boom, head_fn=lambda: HEAD)
    assert URL not in str(info.value) and URL not in caplog.text


def test_no_url_opens_no_connection() -> None:
    def forbidden(s: DbSettings) -> Any:
        raise AssertionError("connection opened")

    check_revision(DbSettings(device_id="dev1"), connect=forbidden, head_fn=lambda: HEAD)


def test_lifespan_off_without_url(app_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTA_DATABASE_URL", raising=False)
    # Without a URL the real check_startup returns before any connection.
    with TestClient(main_module.create_app()) as client:
        assert client.get("/healthz").status_code == 200


def test_lifespan_refuses_start(app_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse() -> None:
        raise RevisionMismatchError("revision differs")

    monkeypatch.setattr(main_module, "check_startup", refuse)
    with pytest.raises(RevisionMismatchError), TestClient(main_module.create_app()):
        pass

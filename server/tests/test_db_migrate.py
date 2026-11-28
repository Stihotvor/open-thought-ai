"""migrate(): call order, lock polling, failure paths. A fake connection records the calls."""

import logging
from typing import Any

import pytest
from alembic.config import Config
from sqlalchemy import Connection
from sqlalchemy.exc import OperationalError

from ota_server.db.migrate import LOCK_APP_KEY, MigrationError, migrate
from ota_server.db.settings import DbSettings

URL = "postgresql+psycopg://user:s3cretpw@db.example:5432/ota"


class FakeResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar(self) -> object:
        return self._value


class FakeConnection:
    def __init__(self, busy_polls: int | None = 0, schema_exists: bool = False) -> None:
        self.calls: list[str] = []
        self.params: list[dict[str, Any]] = []
        self.busy_polls = busy_polls  # None: always busy
        self.schema_exists = schema_exists
        self.polls = 0

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> FakeResult:
        sql = str(statement)
        self.params.append(params or {})
        if "pg_try_advisory_lock" in sql:
            self.polls += 1
            free = self.busy_polls is not None and self.polls > self.busy_polls
            self.calls.append("pg_try_advisory_lock")
            return FakeResult(free)
        if "to_regnamespace" in sql:
            self.calls.append("to_regnamespace")
            return FakeResult("ota_dev" if self.schema_exists else None)
        self.calls.append(self._label(sql))
        return FakeResult(None)

    @staticmethod
    def _label(sql: str) -> str:
        for key in (
            "CREATE SCHEMA",
            "lock_timeout",
            "search_path",
            "statement_timeout",
            "idle_in",
            "pg_advisory_unlock",
        ):
            if key in sql:
                return key
        return sql

    def commit(self) -> None:
        self.calls.append("COMMIT")

    def rollback(self) -> None:
        self.calls.append("rollback")

    def close(self) -> None:
        self.calls.append("close")


@pytest.fixture
def settings() -> DbSettings:
    return DbSettings(device_id="dev", database_url=URL)  # type: ignore[arg-type]


class Harness:
    def __init__(self, conn: FakeConnection, fail: bool = False) -> None:
        self.conn = conn
        self.fail = fail
        self.revisions: list[str] = []

    def connect(self, _settings: DbSettings) -> Connection:
        return self.conn  # type: ignore[return-value]

    def upgrade(self, config: Config, revision: str) -> None:
        self.conn.calls.append("upgrade")
        assert config.attributes["connection"] is self.conn
        assert config.get_main_option("version_table_schema") == "ota_dev"
        assert revision == "head"
        if self.fail:
            raise RuntimeError("boom")

    def downgrade(self, _config: Config, revision: str) -> None:
        self.conn.calls.append("downgrade")
        self.revisions.append(revision)

    def run(self, settings: DbSettings, **kw: Any) -> None:
        migrate(
            settings,
            connect=self.connect,
            upgrade_fn=self.upgrade,
            downgrade_fn=self.downgrade,
            lock_wait_seconds=kw.pop("lock_wait_seconds", 1.0),
            poll_seconds=0.0,
            **kw,
        )


def test_call_order_on_fresh_database(settings: DbSettings) -> None:
    conn = FakeConnection()
    Harness(conn).run(settings)
    assert conn.calls == [
        "pg_try_advisory_lock",
        "COMMIT",
        "statement_timeout",
        "idle_in",
        "to_regnamespace",
        "CREATE SCHEMA",
        "COMMIT",
        "lock_timeout",
        "search_path",
        "COMMIT",
        "upgrade",
        "rollback",
        "pg_advisory_unlock",
        "close",
    ]


def test_existing_schema_is_not_created_again(settings: DbSettings) -> None:
    conn = FakeConnection(schema_exists=True)
    Harness(conn).run(settings)
    assert "CREATE SCHEMA" not in conn.calls


def test_lock_is_polled_with_bound_schema(settings: DbSettings) -> None:
    conn = FakeConnection(busy_polls=2)
    Harness(conn).run(settings)
    assert conn.calls.count("pg_try_advisory_lock") == 3
    polls = [p for p in conn.params if "app" in p and p.get("app") == LOCK_APP_KEY]
    assert all(p["schema"] == "ota_dev" for p in polls)
    assert len(polls) == 4  # three polls and the unlock


def test_lock_wait_exceeds_bound(settings: DbSettings, caplog: pytest.LogCaptureFixture) -> None:
    conn = FakeConnection(busy_polls=None)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(MigrationError):
        Harness(conn).run(settings, lock_wait_seconds=0.05)
    assert "upgrade" not in conn.calls
    assert "pg_advisory_unlock" not in conn.calls
    assert "close" in conn.calls
    assert all(
        "s3cretpw" not in r.getMessage() and "db.example" not in r.getMessage()
        for r in caplog.records
    )


def test_unreachable_database(settings: DbSettings, caplog: pytest.LogCaptureFixture) -> None:
    def connect(_settings: DbSettings) -> Connection:
        raise OperationalError("connect", {}, Exception(f"could not connect to {URL}"))

    caplog.set_level(logging.DEBUG)
    with pytest.raises(MigrationError) as info:
        migrate(settings, connect=connect)
    assert "s3cretpw" not in str(info.value)
    assert "s3cretpw" not in caplog.text
    assert info.value.__cause__ is None


def test_upgrade_fails_still_releases(settings: DbSettings) -> None:
    conn = FakeConnection()
    with pytest.raises(RuntimeError, match="boom"):
        Harness(conn, fail=True).run(settings)
    assert conn.calls[-3:] == ["rollback", "pg_advisory_unlock", "close"]


def test_release_failure_keeps_original_error_and_closes(
    settings: DbSettings, caplog: pytest.LogCaptureFixture
) -> None:
    class BadRelease(FakeConnection):
        def rollback(self) -> None:
            super().rollback()
            raise OSError(URL)

    conn = BadRelease()
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="boom"):
        Harness(conn, fail=True).run(settings)
    assert conn.calls[-1] == "close"
    assert "OSError" in caplog.text
    assert "s3cretpw" not in caplog.text


def test_downgrade_needs_explicit_flag(settings: DbSettings) -> None:
    conn = FakeConnection()
    with pytest.raises(MigrationError):
        Harness(conn).run(settings, downgrade=True)
    assert "downgrade" not in conn.calls
    assert conn.calls == []


def test_downgrade_runs_with_flag(settings: DbSettings) -> None:
    conn = FakeConnection()
    Harness(conn).run(settings, downgrade=True, allow_downgrade=True)
    assert "downgrade" in conn.calls


def test_downgrade_default_target_is_one_step(settings: DbSettings) -> None:
    harness = Harness(FakeConnection())
    harness.run(settings, downgrade=True, allow_downgrade=True)
    assert harness.revisions == ["-1"]


def test_downgrade_target_can_be_set(settings: DbSettings) -> None:
    harness = Harness(FakeConnection())
    harness.run(settings, downgrade=True, allow_downgrade=True, downgrade_target="base")
    assert harness.revisions == ["base"]

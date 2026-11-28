"""Postgres integration fixtures. All tests skip unless OTA_TEST_DATABASE_URL is set."""

import os
import uuid
from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from ota_server.db.names import schema_name
from ota_server.db.settings import DbSettings

TEST_URL_VAR = "OTA_TEST_DATABASE_URL"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "slow: waits more than 10 s (runs by default)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get(TEST_URL_VAR):
        return
    skip = pytest.mark.skip(reason=f"{TEST_URL_VAR} is not set")
    for item in items:
        if "integration" in item.path.parts:
            item.add_marker(skip)


@pytest.fixture
def admin_url() -> str:
    return os.environ[TEST_URL_VAR]


@pytest.fixture
def admin_engine(admin_url: str) -> Iterator[Engine]:
    engine = create_engine(admin_url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    yield engine
    engine.dispose()


@pytest.fixture
def device_id(admin_engine: Engine) -> Iterator[str]:
    """A unique DEVICE_ID per test. The schema is dropped afterwards."""
    ident = "t" + uuid.uuid4().hex[:16]
    yield ident
    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name(ident)}" CASCADE'))


@pytest.fixture
def settings(admin_url: str, device_id: str) -> DbSettings:
    return DbSettings(device_id=device_id, database_url=admin_url)  # type: ignore[arg-type]


@pytest.fixture
def role_url(admin_url: str) -> Callable[[str, str], str]:
    def build(role: str, password: str) -> str:
        url = make_url(admin_url).set(username=role, password=password)
        return url.render_as_string(hide_password=False)

    return build

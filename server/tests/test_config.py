"""Settings (tool-safety T7, D8): the MCP host allowlist from OTA_MCP_ALLOWED_HOSTS."""

from pathlib import Path

import pytest

from ota_server.config import Settings

DIGEST = "0" * 64


@pytest.fixture(autouse=True)
def required(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The required fields, and an empty working directory so no env file is read."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OTA_DEVICE_ID", "test")
    monkeypatch.setenv("OTA_DEVICE_TOKEN_SHA256", DIGEST)
    monkeypatch.delenv("OTA_MCP_ALLOWED_HOSTS", raising=False)


def load() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment


def test_allowlist_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTA_MCP_ALLOWED_HOSTS", " N8N, 10.0.0.5,")
    assert load().allowed_hosts == frozenset({"n8n", "10.0.0.5"})


def test_empty_default() -> None:
    assert load().allowed_hosts == frozenset()

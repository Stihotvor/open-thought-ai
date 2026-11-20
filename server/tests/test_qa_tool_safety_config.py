"""QA cases for the servers file, the allowlist setting and the docs."""

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from ota_server.config import Settings
from ota_server.mcp.config import McpServerConfig, load_servers

PIN = "sha256:" + "a" * 64
SECURITY_DOC = Path(__file__).resolve().parents[2] / "docs" / "security.md"


def entry(transport: str = "http", **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"name": "n8n", "transport": transport, "tools": {"echo": PIN}}
    if transport == "stdio":
        base |= {"command": "mcp-server", "args": []}
    else:
        base["url"] = "https://host.example/mcp"
    return base | over


def load(
    tmp_path: Path, entries: list[dict[str, Any]], caplog: pytest.LogCaptureFixture
) -> tuple[list[McpServerConfig], list[str]]:
    path = tmp_path / "servers.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    with caplog.at_level(logging.DEBUG):
        servers = load_servers(str(path))
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    return servers, errors


def test_qa_027_non_http_scheme_is_rejected(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    servers, errors = load(tmp_path, [entry(url="ftp://tools.example.org/mcp")], caplog)
    assert servers == [] and errors


def test_qa_042_literal_header_value_is_rejected(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    servers, errors = load(tmp_path, [entry(headers={"Authorization": "Bearer abc123"})], caplog)
    assert servers == [] and errors
    assert "abc123" not in caplog.text


def test_qa_044_same_secret_twice_in_one_entry_loads(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Actual behavior: allowed. The docs leave it open, so the result is partial."""
    headers = {"X-Key": "MCP_SECRET_N8N", "X-Key2": "MCP_SECRET_N8N"}
    servers, errors = load(tmp_path, [entry(headers=headers)], caplog)
    assert [s.name for s in servers] == ["n8n"] and not errors


def test_qa_045_lowercase_secret_name_is_rejected(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    servers, errors = load(tmp_path, [entry(headers={"X-Key": "mcp_secret_n8n"})], caplog)
    assert servers == [] and errors


def test_qa_049_stdio_env_keys_matched_case_insensitively(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    keys = ["my_token", "DbPassword", "Secret_Thing", "x_auth_y"]
    entries = [entry("stdio", name=f"s{i}", env={k: "v"}) for i, k in enumerate(keys)]
    servers, errors = load(tmp_path, entries, caplog)
    assert servers == [] and len(errors) == 4


def test_qa_051_harmless_stdio_env_loads(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    servers, errors = load(
        tmp_path, [entry("stdio", env={"LANG": "C", "DATA_DIR": "/data"})], caplog
    )
    assert len(servers) == 1 and not errors


def test_qa_054_bad_pin_format_is_rejected(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    entries = [
        entry(name="a", tools={"echo": "sha256:abc"}),
        entry(name="b", tools={"echo": "md5:" + "a" * 32}),
    ]
    servers, errors = load(tmp_path, entries, caplog)
    assert servers == [] and len(errors) == 2


def test_qa_055_uppercase_hex_pin(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Actual behavior: rejected (the pin pattern is lowercase hex). The docs leave it open."""
    servers, errors = load(tmp_path, [entry(tools={"echo": "sha256:" + "A" * 64})], caplog)
    assert servers == [] and errors


def test_qa_086_allowlist_of_commas_and_blanks_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTA_DEVICE_ID", "test")
    monkeypatch.setenv("OTA_DEVICE_TOKEN_SHA256", "0" * 64)
    monkeypatch.setenv("OTA_MCP_ALLOWED_HOSTS", " , ,")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.allowed_hosts == frozenset()


def test_qa_091_docs_name_the_new_format() -> None:
    text = SECURITY_DOC.read_text(encoding="utf-8")
    for needle in ("`tools` map", "MCP_SECRET_", "OTA_MCP_ALLOWED_HOSTS"):
        assert needle in text

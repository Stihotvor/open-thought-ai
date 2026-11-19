"""T1: MCP server config loader, default hooks and the Settings field (D8, D11)."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from ota_server.config import Settings
from ota_server.mcp.config import McpServerConfig, load_servers
from ota_server.mcp.hooks import AllowAll, ConnectionPolicy, ToolFilter
from ota_server.mcp.port import RawTool

DIGEST = "0" * 64


def _entry(transport: str = "http", **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"name": "n8n", "transport": transport, "timeout": 5, "tools": ["a"]}
    if transport == "stdio":
        base |= {"command": "mcp-server", "args": ["--x"]}
    else:
        base["url"] = "https://host/mcp"
    return base | over


def _write(tmp_path: Path, data: Any) -> str:
    path = tmp_path / "servers.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("transport", ["http", "sse", "stdio"])
def test_valid_entries_load(tmp_path: Path, transport: str) -> None:
    servers = load_servers(_write(tmp_path, [_entry(transport)]))
    assert len(servers) == 1
    srv = servers[0]
    assert isinstance(srv, McpServerConfig)
    assert (srv.name, srv.transport, srv.timeout, srv.tools) == ("n8n", transport, 5.0, ["a"])


def test_no_file_configured() -> None:
    assert load_servers("") == []


def test_bad_json(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR):
        assert load_servers(_write(tmp_path, "{not json")) == []
    assert any(r.levelno == logging.ERROR for r in caplog.records)


BAD_ENTRIES: dict[str, dict[str, Any]] = {
    "duplicate": _entry(),
    "bad_name": _entry(name="bad name!"),
    "sep_name": _entry(name="a__b"),
    "user_info": _entry(name="x", url="https://user:pw@host/mcp"),
    "query": _entry(name="x", url="https://host/mcp?token=x"),
    "timeout_low": _entry(name="x", timeout=0.5),
    "timeout_high": _entry(name="x", timeout=61),
    "transport_ws": _entry(name="x", transport="ws"),
    "stdio_no_command": {k: v for k, v in _entry("stdio", name="x").items() if k != "command"},
}


@pytest.mark.parametrize("defect", list(BAD_ENTRIES))
def test_bad_entry_skipped_good_survives(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, defect: str
) -> None:
    with caplog.at_level(logging.ERROR):
        servers = load_servers(_write(tmp_path, [_entry(), BAD_ENTRIES[defect]]))
    assert [s.name for s in servers] == ["n8n"]
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors
    assert "1" in errors[0].getMessage()  # the entry index


@pytest.mark.parametrize("transport", ["http", "sse"])
def test_env_rejected_for_remote(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, transport: str
) -> None:
    with caplog.at_level(logging.ERROR):
        servers = load_servers(_write(tmp_path, [_entry(transport, env={"FOO": "bar"})]))
    assert servers == []
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_env_allowed_for_stdio(tmp_path: Path) -> None:
    servers = load_servers(_write(tmp_path, [_entry("stdio", env={"FOO": "bar"})]))
    assert len(servers) == 1
    assert servers[0].env == {"FOO": "bar"}


def test_allowlist_name_failing_d13(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR):
        servers = load_servers(_write(tmp_path, [_entry(tools=["ok", "bad name"])]))
    assert servers[0].tools == ["ok"]
    assert any("bad name" in r.getMessage() for r in caplog.records)


def test_default_hooks() -> None:
    policy: ConnectionPolicy = AllowAll()
    tool_filter: ToolFilter = AllowAll()
    config = McpServerConfig(name="n", transport="http", url="http://h/", tools=["t"])
    assert asyncio.run(policy.before_connect(config)) == {}
    assert tool_filter.admit("n", RawTool(name="t", description="", schema={})) is True


def test_settings_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTA_MCP_SERVERS_FILE", "/x/servers.json")
    settings = Settings(device_id="test", device_token_sha256=DIGEST, _env_file=None)  # type: ignore[call-arg]
    assert settings.mcp_servers_file == "/x/servers.json"
    monkeypatch.delenv("OTA_MCP_SERVERS_FILE")
    settings = Settings(device_id="test", device_token_sha256=DIGEST, _env_file=None)  # type: ignore[call-arg]
    assert settings.mcp_servers_file == ""

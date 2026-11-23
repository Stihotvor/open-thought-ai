"""mcp-client QA: config file, config variable and fields, stdio details (cases.md)."""

import json
from contextlib import AsyncExitStack
from pathlib import Path

import pytest
from mcp_fakes import assert_pid_gone
from mcp_qa_support import (  # pylint: disable=unused-import
    LONG_TOOL,
    PIN,
    body,
    call,
    errors,
    loopback_only,  # noqa: F401  (autouse T9 guard, active in this module)
    names,
    new_session,
    run,
    serving,
    start_file,
    stdio_config,
    stdio_entry,
    tool_names,
)

from ota_server.config import Settings
from ota_server.main import start_mcp_sources
from ota_server.mcp.config import load_servers
from ota_server.mcp.registry import McpRegistry

# ---------------------------------------------------------------- config file


def test_qa_012_invalid_json(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            sources = await start_file(tmp_path, "{not json", stack)
            assert sources == []
            assert errors(caplog)
            session = new_session(sources)
            assert body(await call(session, "device__get_status")) == "up"

    run(scenario())


def test_qa_013_one_bad_entry_skipped(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    bad = {"name": "b", "transport": "ftp", "url": "ftp://x/", "tools": {"echo": PIN}}

    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            entries = [stdio_entry("a", ["echo"]), bad, stdio_entry("c", ["echo"])]
            sources = await start_file(tmp_path, entries, stack)
            assert any("'b'" in e for e in errors(caplog))
            assert tool_names(sources) == ["a__echo", "c__echo"]

    run(scenario())


def test_qa_014_duplicate_name(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            first = server.entry("notes", ["add"])
            second = {**first, "url": "http://127.0.0.1:9/mcp"}
            sources = await start_file(tmp_path, [first, second], stack)
            assert any("duplicate" in e for e in errors(caplog))
            assert tool_names(sources) == ["notes__add"]
            assert server.inits == 1

    run(scenario())


def test_qa_015_bad_server_names(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            entries = [stdio_entry(n, ["echo"]) for n in ("my__srv", "bad name!", "n" * 70)]
            assert await start_file(tmp_path, entries, stack) == []
            assert len(errors(caplog)) >= 3

    run(scenario())


def test_qa_016_bad_tool_name(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            entry = server.entry("notes", ["bad.tool", "ok_tool"])
            entry["no_confirm"] = ["ok_tool"]  # a dropped name in no_confirm rejects the entry
            sources = await start_file(tmp_path, [entry], stack)
            assert tool_names(sources) == ["notes__ok_tool"]
            assert any("bad.tool" in e for e in errors(caplog))

    run(scenario())


def test_qa_017_combined_name_too_long(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            entry = server.entry("notes", [LONG_TOOL, "ok_tool"])
            sources = await start_file(tmp_path, [entry], stack)
            assert tool_names(sources) == ["notes__ok_tool"]
            assert any(LONG_TOOL in e for e in errors(caplog))

    run(scenario())


# ---------------------------------------------------------------- config variable and fields


def test_qa_030_missing_variable(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            sources = await start_mcp_sources(Settings.model_construct(mcp_servers_file=""), stack)
            assert sources == []
            assert errors(caplog) == []
            assert "device__get_status" in names(new_session(sources))

    run(scenario())


def test_qa_031_path_missing(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            settings = Settings.model_construct(mcp_servers_file="/nonexistent/servers.json")
            sources = await start_mcp_sources(settings, stack)
            assert sources == []
            assert errors(caplog)
            assert body(await call(new_session(sources), "device__get_status")) == "up"

    run(scenario())


def test_qa_032_timeout_bounds(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            low = {**stdio_entry("low", ["echo"]), "timeout": 0.5}
            high = {**stdio_entry("high", ["echo"]), "timeout": 61}
            sources = await start_file(tmp_path, [low, high, stdio_entry("dflt", ["echo"])], stack)
            assert len(errors(caplog)) >= 2
            assert [s.name for s in sources] == ["dflt"]
            assert sources[0].timeout == 10

    run(scenario())


def test_qa_033_url_userinfo_or_query(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            urls = ["http://user:pw@127.0.0.1:8000/mcp", "http://127.0.0.1:8000/mcp?token=abc"]
            entries = [
                {"name": f"u{i}", "transport": "http", "url": u, "tools": {"add": PIN}}
                for i, u in enumerate(urls)
            ]
            assert await start_file(tmp_path, entries, stack) == []
            assert len(errors(caplog)) >= 2
            assert "user:pw@" not in caplog.text

    run(scenario())


def test_qa_034_non_http_scheme(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    entry = {"name": "f", "transport": "http", "url": "file:///etc/passwd", "tools": {"x": PIN}}
    path = tmp_path / "s.json"
    path.write_text(json.dumps([entry]))
    assert load_servers(str(path)) == []
    assert errors(caplog)


def test_qa_035_missing_fields(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    no_command = {"name": "a", "transport": "stdio", "args": [], "tools": {"x": PIN}}
    no_url = {"name": "b", "transport": "http", "tools": {"x": PIN}}
    no_tools = {k: v for k, v in stdio_entry("c", ["x"]).items() if k != "tools"}
    path = tmp_path / "s.json"
    path.write_text(json.dumps([no_command, no_url, no_tools]))
    assert load_servers(str(path)) == []
    assert len(errors(caplog)) >= 3


# ---------------------------------------------------------------- stdio details


def test_qa_036_child_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OTA_TOKEN", "abc")
    monkeypatch.setenv("MCP_SECRET_X", "zzz")

    async def scenario() -> str:
        async with AsyncExitStack() as stack:
            cfg = stdio_config("local", ["env_vars"], env={"MODE": "fast"})
            sources = await McpRegistry.start([cfg], stack)
            return body(await call(new_session(sources), "local__env_vars"))

    env = run(scenario())
    assert "MODE=fast" in env.splitlines()
    assert any(line.startswith("PATH=") for line in env.splitlines())
    for secret in ("OPENAI_API_KEY", "OTA_TOKEN", "MCP_SECRET_X"):
        assert secret not in env


def test_qa_037_stdio_args() -> None:
    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            cfg = stdio_config("local", ["whoami"], "--name", "x")
            sources = await McpRegistry.start([cfg], stack)
            assert tool_names(sources) == ["local__whoami"]
            assert body(await call(new_session(sources), "local__whoami")) == "x"

    run(scenario())


def test_qa_038_connections_shared() -> None:
    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            sources = await McpRegistry.start([stdio_config("local", ["pid"])], stack)
            pids = set()
            for _ in range(3):
                pids.add(body(await call(new_session(sources), "local__pid")))
            assert len(pids) == 1

    run(scenario())


def test_qa_039_zero_allowed_tools(tmp_path: Path) -> None:
    async def scenario() -> list[int]:
        files = [tmp_path / "p1", tmp_path / "p2"]
        async with AsyncExitStack() as stack:
            e1 = stdio_entry("ghosty", ["ghost"], env={"QA_PIDFILE": str(files[0])})
            e2 = stdio_entry("empty", [], env={"QA_PIDFILE": str(files[1])})
            sources = await start_file(tmp_path, [e1, e2], stack)
            assert sources == []
            return [int(f.read_text()) for f in files]

    for pid in run(scenario()):
        assert_pid_gone(pid)

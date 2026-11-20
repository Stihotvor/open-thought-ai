"""mcp-client QA: transports, allowlist, result mapping, hub limits, seam, security (cases.md)."""

import logging
import socket
import tomllib
from contextlib import AsyncExitStack
from pathlib import Path

import pytest
from mcp_qa_support import (  # pylint: disable=unused-import
    SERVER_DIR,
    body,
    call,
    loopback_only,  # noqa: F401  (autouse T9 guard, active in this module)
    names,
    new_session,
    run,
    serving,
    start_file,
    stdio_config,
    stdio_entry,
    tool_names,
    warnings,
)

from ota_server.mcp.config import McpServerConfig
from ota_server.mcp.hooks import ConnectionDenied
from ota_server.mcp.port import RawTool
from ota_server.mcp.registry import McpRegistry

# ---------------------------------------------------------------- functional: transports


def test_qa_001_http_list_and_call() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            sources = await McpRegistry.start([server.config("notes", ["add"])], stack)
            session = new_session(sources)
            out = await call(session, "notes__add", a=2, b=3)
            assert out.ok
            assert body(out) == "5"
            assert [n for n in names(session) if n.startswith("notes__")] == ["notes__add"]

    run(scenario())


def test_qa_002_sse_list_and_call() -> None:
    async def scenario() -> None:
        async with serving("sse") as server, AsyncExitStack() as stack:
            sources = await McpRegistry.start([server.config("weather", ["forecast"])], stack)
            out = await call(new_session(sources), "weather__forecast", city="Kyiv")
            assert body(out) == "sunny"

    run(scenario())


def test_qa_003_stdio_list_and_call() -> None:
    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            sources = await McpRegistry.start([stdio_config("local", ["echo"])], stack)
            out = await call(new_session(sources), "local__echo", text="hello")
            assert body(out) == "hello"

    run(scenario())


# ---------------------------------------------------------------- allowlist


def test_qa_004_unlisted_tool_hidden() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            sources = await McpRegistry.start([server.config("notes", ["add"])], stack)
            listed = names(new_session(sources))
            assert "notes__add" in listed
            assert "notes__delete_all" not in listed

    run(scenario())


def test_qa_005_unlisted_call_unknown() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            sources = await McpRegistry.start([server.config("notes", ["add"])], stack)
            out = await call(new_session(sources), "notes__delete_all")
            assert (out.ok, out.code) == (False, "unknown_tool")
            assert "delete_all" not in server.calls
            # the source itself is also closed to a direct call
            direct = await sources[0].call("delete_all", {})
            assert direct.code == "unknown_tool"
            assert server.calls == []

    run(scenario())


def test_qa_006_allowlist_missing_tool(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            sources = await McpRegistry.start([server.config("notes", ["add", "ghost"])], stack)
            assert any("ghost" in w for w in warnings(caplog))
            out = await call(new_session(sources), "notes__add", a=1, b=2)
            assert body(out) == "3"

    run(scenario())


# ---------------------------------------------------------------- result mapping


def test_qa_009_is_error_generic() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            sources = await McpRegistry.start([server.config("notes", ["boom"])], stack)
            out = await call(new_session(sources), "notes__boom")
            assert not out.ok
            assert "hunter2" not in out.text
            assert "/srv/app.py" not in out.text
            assert len(out.text) < 200

    run(scenario())


def test_qa_010_image_replaced() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            sources = await McpRegistry.start([server.config("notes", ["chart"])], stack)
            out = await call(new_session(sources), "notes__chart")
            assert body(out) == "Chart:\n[image]"
            assert "PNG" not in out.text

    run(scenario())


def test_qa_011_resource_replaced() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            sources = await McpRegistry.start([server.config("notes", ["res"])], stack)
            out = await call(new_session(sources), "notes__res")
            assert "[resource]" in out.text
            assert "RESOURCEBODY" not in out.text

    run(scenario())


# ---------------------------------------------------------------- hub limits


def test_qa_040_long_result_truncated() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            sources = await McpRegistry.start([server.config("notes", ["big"])], stack)
            out = await call(new_session(sources), "notes__big")
            assert out.ok
            assert len(out.text) == 2000

    run(scenario())


def test_qa_041_device_tools_unchanged() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            sources = await McpRegistry.start([server.config("notes", ["add"])], stack)
            session = new_session(sources)
            assert body(await call(session, "device__get_status")) == "up"
            listed = names(session)
            assert {"device__set_volume", "device__get_status", "notes__add"} <= set(listed)

    run(scenario())


# ---------------------------------------------------------------- seam (#31)


class DenyAll:
    async def before_connect(self, config: McpServerConfig) -> dict[str, str]:
        raise ConnectionDenied(config.name)


class HeaderPolicy:
    async def before_connect(self, config: McpServerConfig) -> dict[str, str]:
        return {"X-Test": "1"}


class RejectAdd:
    def admit(self, server: str, tool: RawTool) -> bool:
        return tool.name != "add"


def test_qa_042_connection_denied(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            cfg = server.config("notes", ["add"])
            sources = await McpRegistry.start([cfg], stack, policy=DenyAll())
            assert sources == []
            assert server.inits == 0
            assert any("notes" in w for w in warnings(caplog))

    run(scenario())


def test_qa_043_filter_and_headers() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            sources = await McpRegistry.start(
                [server.config("notes", ["add", "echo"])],
                stack,
                policy=HeaderPolicy(),
                tool_filter=RejectAdd(),
            )
            assert any(h.get("x-test") == "1" for h in server.headers)
            assert tool_names(sources) == ["notes__echo"]

    run(scenario())


def test_qa_044_n8n_entry_config_only(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            sources = await start_file(tmp_path, [server.entry("n8n", ["send_mail"])], stack)
            assert tool_names(sources) == ["n8n__send_mail"]

    run(scenario())


# ---------------------------------------------------------------- gate and security


def test_qa_045_quality_gate_is_run_outside_pytest() -> None:
    pytest.skip("NOT AUTOMATED here: the gate (ruff, mypy, pytest --cov) is run as commands")


def test_qa_046_sdk_pin() -> None:
    project = tomllib.loads((SERVER_DIR / "pyproject.toml").read_text())
    pins = [d for d in project["project"]["dependencies"] if d.replace(" ", "").startswith("mcp")]
    assert pins == ["mcp==1.30.0"]
    lock = tomllib.loads((SERVER_DIR / "uv.lock").read_text())
    versions = [p["version"] for p in lock["package"] if p["name"] == "mcp"]
    assert versions == ["1.30.0"]


def test_qa_047_loopback_only() -> None:
    with pytest.raises(OSError, match="non-loopback"):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("8.8.8.8", 53))


def test_qa_048_secret_value_not_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            entry = stdio_entry("local", ["echo"], env={"API_KEY": "sk-live-123"})
            sources = await start_file(tmp_path, [entry], stack)
            assert tool_names(sources) == ["local__echo"]
            await call(new_session(sources), "local__echo", text="x")

    run(scenario())
    assert "sk-live-123" not in caplog.text

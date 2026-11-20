"""mcp-client QA: startup failures, concurrency, timeout, reconnect, stdio lifecycle (cases.md)."""

import asyncio
import logging
import os
import signal
import sys
import time
from contextlib import AsyncExitStack
from pathlib import Path

import pytest
from mcp_fakes import assert_pid_gone
from mcp_qa_support import (  # pylint: disable=unused-import
    RAW,
    QaServer,
    body,
    call,
    loopback_only,  # noqa: F401  (autouse T9 guard, active in this module)
    names,
    new_session,
    run,
    serving,
    start_file,
    stdio_config,
    warnings,
)

from ota_server.mcp.adapters import SdkConnection
from ota_server.mcp.config import McpServerConfig
from ota_server.mcp.hooks import ConnectionPolicy
from ota_server.mcp.port import McpConnection
from ota_server.mcp.registry import McpRegistry
from ota_server.mcp.source import McpSource

# ---------------------------------------------------------------- startup failures


def test_qa_007_server_down_at_startup(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    async def scenario() -> None:
        async with serving() as up, AsyncExitStack() as stack:
            down = {"name": "down", "transport": "http", "url": "http://127.0.0.1:9/mcp",
                    "tools": ["ping"]}  # fmt: skip
            sources = await start_file(tmp_path, [up.entry("up", ["ping"]), down], stack)
            assert any("down" in w for w in warnings(caplog))
            listed = names(new_session(sources))
            assert "up__ping" in listed
            assert "device__get_status" in listed
            assert not [n for n in listed if n.startswith("down__")]

    run(scenario())


def test_qa_008_down_stays_off(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = QaServer()
        async with AsyncExitStack() as stack:
            sources = await start_file(tmp_path, [server.entry("down", ["ping"])], stack)
            await server.start()
            try:
                assert not [n for n in names(new_session(sources)) if n.startswith("down__")]
                assert server.inits == 0  # nobody retried
            finally:
                await server.stop()

    run(scenario())


# ---------------------------------------------------------------- concurrency, timeout


def test_qa_018_concurrent_sessions() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            sources = await McpRegistry.start([server.config("notes", ["slow_echo"])], stack)
            a, b = new_session(sources), new_session(sources)
            out_a, out_b = await asyncio.gather(
                call(a, "notes__slow_echo", text="alpha"), call(b, "notes__slow_echo", text="beta")
            )
            assert (body(out_a), body(out_b)) == ("alpha", "beta")
            assert server.inits == 1

    run(scenario())


def test_qa_019_timeout() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            sources = await McpRegistry.start([server.config("notes", ["sleep30"], 2)], stack)
            start = time.monotonic()
            out = await call(new_session(sources), "notes__sleep30")
            assert time.monotonic() - start <= 2.5
            assert (out.ok, out.code) == (False, "tool_timeout")

    run(scenario())


def test_qa_020_call_after_timeout() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            cfg = server.config("notes", ["sleep30", "add"], 2)
            sources = await McpRegistry.start([cfg], stack)
            session = new_session(sources)
            assert (await call(session, "notes__sleep30")).code == "tool_timeout"
            assert body(await call(session, "notes__add", a=1, b=1)) == "2"
            assert server.inits == 1

    run(scenario())


# ---------------------------------------------------------------- http reconnect


async def recording_start(
    configs: list[McpServerConfig], stack: AsyncExitStack, conns: list[SdkConnection]
) -> list[McpSource]:
    async def connect(config: McpServerConfig, policy: ConnectionPolicy) -> McpConnection:
        conn = await SdkConnection.open(config, policy)
        conns.append(conn)
        return conn

    return await McpRegistry.start(configs, stack, connect=connect)


def test_qa_021_http_restart_reconnect() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            conns: list[SdkConnection] = []
            sources = await recording_start([server.config("notes", ["add"])], stack, conns)
            await server.restart()
            out = await call(new_session(sources), "notes__add", a=4, b=4)
            assert body(out) == "8"
            assert conns[0].generation == 1
            assert server.inits == 2

    run(scenario())


def test_qa_022_reconnect_fails() -> None:
    async def scenario() -> None:
        server = QaServer()
        await server.start()
        async with AsyncExitStack() as stack:
            sources = await McpRegistry.start([server.config("notes", ["add"])], stack)
            await server.stop()
            session = new_session(sources)
            out = await call(session, "notes__add", a=1, b=1)
            assert not out.ok
            assert out.code == "mcp_error"
            assert body(await call(session, "device__get_status")) == "up"

    run(scenario())


def test_qa_023_concurrent_failed_calls_one_reconnect() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            conns: list[SdkConnection] = []
            sources = await recording_start([server.config("notes", ["add"])], stack, conns)
            await server.restart()
            a, b = new_session(sources), new_session(sources)
            out_a, out_b = await asyncio.gather(
                call(a, "notes__add", a=1, b=2), call(b, "notes__add", a=3, b=4)
            )
            assert (body(out_a), body(out_b)) == ("3", "7")
            assert conns[0].generation == 1
            assert server.inits == 2

    run(scenario())


# ---------------------------------------------------------------- stdio lifecycle


def test_qa_024_stdio_child_restarts() -> None:
    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            sources = await McpRegistry.start([stdio_config("local", ["echo", "pid"])], stack)
            session = new_session(sources)
            old = int(body(await call(session, "local__pid")))
            os.kill(old, signal.SIGKILL)
            assert_pid_gone(old)
            assert body(await call(session, "local__echo", text="again")) == "again"
            assert int(body(await call(session, "local__pid"))) != old

    run(scenario())


def test_qa_025_stdio_keeps_crashing(tmp_path: Path) -> None:
    flag = tmp_path / "crash"

    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            cfg = stdio_config("local", ["echo", "pid"], "--crash-if", str(flag))
            sources = await McpRegistry.start([cfg], stack)
            session = new_session(sources)
            pid = int(body(await call(session, "local__pid")))
            flag.write_text("x")
            os.kill(pid, signal.SIGKILL)
            assert_pid_gone(pid)
            for _ in range(2):
                out = await call(session, "local__echo", text="x")
                assert not out.ok
            assert body(await call(session, "device__get_status")) == "up"

    run(scenario())


def test_qa_026_shutdown_leaves_no_children(tmp_path: Path) -> None:
    async def scenario() -> list[int]:
        pids: list[int] = []
        async with serving() as server:
            async with AsyncExitStack() as stack:
                configs = [stdio_config(f"s{i}", ["pid"]) for i in range(2)]
                sources = await McpRegistry.start([*configs, server.config("n", ["add"])], stack)
                session = new_session(sources)
                for i in range(2):
                    pids.append(int(body(await call(session, f"s{i}__pid"))))
                assert len(set(pids)) == 2
            return pids

    for pid in run(scenario()):
        assert_pid_gone(pid)


def test_qa_027_shutdown_with_stubborn_child() -> None:
    async def scenario() -> tuple[int, float]:
        stack = AsyncExitStack()
        sources = await McpRegistry.start([stdio_config("local", ["pid"], "--stubborn")], stack)
        pid = int(body(await call(new_session(sources), "local__pid")))
        start = time.monotonic()
        await stack.aclose()
        return pid, time.monotonic() - start

    pid, elapsed = run(scenario())
    assert elapsed <= 5 + 3
    assert_pid_gone(pid)


def test_qa_028_malformed_reply_becomes_error(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async with AsyncExitStack() as stack:
            cfg = McpServerConfig(
                "raw", "stdio", command=sys.executable, args=[RAW], tools=["boom"], timeout=5
            )
            sources = await McpRegistry.start([cfg], stack)
            out = await call(new_session(sources), "raw__boom")
            assert not out.ok
            assert out.code in ("mcp_error", "tool_timeout")

    run(scenario())


def test_qa_029_caller_cancel() -> None:
    async def scenario() -> None:
        async with serving() as server, AsyncExitStack() as stack:
            cfg = server.config("notes", ["sleep30", "add"], 30)
            sources = await McpRegistry.start([cfg], stack)
            session = new_session(sources)
            task = asyncio.create_task(call(session, "notes__sleep30"))
            await asyncio.sleep(1)
            start = time.monotonic()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert time.monotonic() - start < 1
            assert body(await call(session, "notes__add", a=1, b=2)) == "3"

    run(scenario())

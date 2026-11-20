"""MCP client end to end (T6): lifespan -> McpRegistry -> gateway -> DeviceSession -> McpHub.
A fake chat asks for tools; a FastMCP fake server or a stdio child serves them; the
TestClient socket plays the device. No board, no real API (B1, B2, E1, M4)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import mcp_fakes
import pytest
from fakes import SPEECH, Endpoint, pipeline_for, sse, tts_wav
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server.fastmcp import FastMCP
from mcp_fakes import STDIO_SCRIPT, FakeServer, assert_pid_gone, free_port
from starlette.testclient import WebSocketTestSession

from ota_server import gateway
from ota_server.config import get_settings
from ota_server.main import create_app
from ota_server.protocol import AUDIO_CHUNK, MAX_CREDIT, START_CREDIT
from ota_server.session import DeviceSession

TOKEN = "test-token-0123456789abcdef0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
TOOL = "tool"  # a chat script step: answer with tool_calls
ECHO = ("fake__echo", '{"text": "hello mcp"}')
VOLUME = ("device__set_volume", '{"level": 30}')
REPLY = "Gotowe."
PIN = "sha256:" + "0" * 64  # T2 pin format; filtering by pin starts in T3


def tool_sse(calls: list[tuple[str, str]]) -> str:
    """The streamed OpenAI tool_calls format that pipeline.py reads."""
    lines = []
    for index, (name, arguments) in enumerate(calls):
        call = {
            "index": index,
            "id": f"call_{index}",
            "function": {"name": name, "arguments": arguments},
        }
        lines.append("data: " + json.dumps({"choices": [{"delta": {"tool_calls": [call]}}]}))
    return "\n\n".join([*lines, "data: [DONE]"]) + "\n\n"


class ScriptChat(Endpoint):
    """The fake endpoint: the first chat asks for `call`, the second gives the final text."""

    def __init__(self, call: tuple[str, str]) -> None:
        super().__init__(reply=REPLY, tts=tts_wav(0.05))
        self.call = call

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if not req.url.path.endswith("/chat/completions"):
            return super().__call__(req)
        self.calls.append("completions")
        self.chats.append(json.loads(req.content)["messages"])
        if len(self.chats) == 1:
            return httpx.Response(200, text=tool_sse([self.call]))
        return httpx.Response(200, text=sse(self.reply))


def counting_build(seen: list[str]) -> Callable[..., FastMCP]:
    """A build_fake for FakeServer whose echo records each call."""

    def build(name: str = "fake") -> FastMCP:
        server = FastMCP(name)

        @server.tool()
        def echo(text: str) -> str:
            seen.append(text)
            return text

        return server

    return build


@contextmanager
def served(transport: str) -> Iterator[FakeServer]:
    """A FakeServer on its own event loop thread: the TestClient app runs on another loop."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    server = FakeServer(transport)
    try:
        asyncio.run_coroutine_threadsafe(server.start(), loop).result(10)
        yield server
    finally:
        asyncio.run_coroutine_threadsafe(server.stop(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        loop.close()


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("OTA_DEVICE_ID", "test")
    monkeypatch.setenv("OTA_DEVICE_TOKEN_SHA256", hashlib.sha256(TOKEN.encode()).hexdigest())
    monkeypatch.setenv("OTA_PIPELINE", "echo")
    monkeypatch.setenv("OTA_MCP_SERVERS_FILE", "")
    get_settings.cache_clear()
    gateway.slot = gateway.SessionSlot()  # no session left over from an earlier test
    yield
    get_settings.cache_clear()


def use_servers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, entries: list[Any]) -> None:
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    monkeypatch.setenv("OTA_MCP_SERVERS_FILE", str(path))
    get_settings.cache_clear()


def record_sessions(monkeypatch: pytest.MonkeyPatch) -> list[DeviceSession]:
    made: list[DeviceSession] = []

    class Recorded(DeviceSession):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            made.append(self)

    monkeypatch.setattr(gateway, "DeviceSession", Recorded)
    return made


def connect(client: TestClient) -> Any:
    return client.websocket_connect("/v1/device", headers=AUTH)


def hello(ws: WebSocketTestSession) -> None:
    ws.send_json({"type": "hello", "proto": 1, "fw": "test", "device_id": "test"})
    assert ws.receive_json()["type"] == "hello"


def voice_turn(ws: WebSocketTestSession) -> tuple[list[dict[str, Any]], bytes]:
    """Speak one turn, answer each tool.call with ok, play the reply; return frames and audio."""
    hello(ws)
    ws.send_json({"type": "listen.start"})
    turn = ws.receive_json()["turn"]
    for i in range(0, len(SPEECH), AUDIO_CHUNK):
        ws.send_bytes(bytes([turn]) + SPEECH[i : i + AUDIO_CHUNK])
    ws.send_json({"type": "listen.stop", "turn": turn})
    ws.send_json({"type": "audio.credit", "bytes": MAX_CREDIT - START_CREDIT})
    return play(ws)


def play(ws: WebSocketTestSession) -> tuple[list[dict[str, Any]], bytes]:
    frames: list[dict[str, Any]] = []
    audio = b""
    while True:
        msg = ws.receive()
        if msg.get("bytes"):
            audio += msg["bytes"][1:]
            ws.send_json({"type": "audio.credit", "bytes": len(msg["bytes"]) - 1})
            continue
        frame = json.loads(msg["text"])
        frames.append(frame)
        if frame["type"] == "tool.call":
            ws.send_json({"type": "tool.result", "id": frame["id"], "ok": True, "result": "v30"})
        if frame["type"] == "reply.end":
            return frames, audio


def tool_messages(chat: list[dict[str, Any]]) -> list[str]:
    return [m["content"] for m in chat if m["role"] == "tool"]


@contextmanager
def run_app(chat: ScriptChat) -> Iterator[tuple[FastAPI, TestClient]]:
    app = create_app()
    with TestClient(app) as client:
        app.state.reply_source = pipeline_for(chat)  # the lifespan ran: swap in the fake chat
        yield app, client


def test_voice_turn_calls_an_mcp_tool(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[str] = []
    monkeypatch.setattr(mcp_fakes, "build_fake", counting_build(seen))
    chat = ScriptChat(ECHO)
    with served("http") as server:
        entry = {"name": "fake", "transport": "http", "url": server.url, "tools": {"echo": PIN}}
        use_servers(monkeypatch, tmp_path, [entry])
        with run_app(chat) as (app, client), connect(client) as ws:
            assert [s.name for s in app.state.mcp_sources] == ["fake"]
            frames, audio = voice_turn(ws)
    assert seen == ["hello mcp"]
    assert len(chat.chats) == 2
    assert any("hello mcp" in text for text in tool_messages(chat.chats[1]))
    assert not [f for f in frames if f["type"] == "tool.call"]  # no device call
    assert audio
    assert frames[-1]["reason"] == "done"


def test_server_down_device_tools_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    url = f"http://127.0.0.1:{free_port()}/mcp"  # nothing listens here
    entry = {"name": "down", "transport": "http", "url": url, "tools": {"echo": PIN}}
    use_servers(monkeypatch, tmp_path, [entry])
    chat = ScriptChat(VOLUME)
    caplog.set_level(logging.WARNING, logger="ota_server.mcp.registry")
    with run_app(chat) as (app, client), connect(client) as ws:
        assert app.state.mcp_sources == []
        frames, audio = voice_turn(ws)
    assert any("down" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)
    calls = [f for f in frames if f["type"] == "tool.call"]
    assert [c["name"] for c in calls] == ["set_volume"]
    assert tool_messages(chat.chats[1])[0].endswith("v30")
    assert audio
    assert frames[-1]["reason"] == "done"


def test_no_config_file_keeps_device_tools_only(monkeypatch: pytest.MonkeyPatch) -> None:
    made = record_sessions(monkeypatch)
    app = create_app()
    with TestClient(app) as client, connect(client) as ws:
        hello(ws)
        assert app.state.mcp_sources == []
        [session] = made
        names = [tool["function"]["name"] for tool in session.hub.list_tools()]
    assert names and all(name.startswith("device__") for name in names)


def test_shutdown_stops_stdio_children(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    entry = {
        "name": "local",
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(STDIO_SCRIPT)],
        "tools": {"pid": PIN},
    }
    use_servers(monkeypatch, tmp_path, [entry])
    app = create_app()
    with TestClient(app) as client:
        [source] = app.state.mcp_sources
        assert client.portal is not None
        outcome = client.portal.call(source.call, "pid", {})
        assert outcome.ok
        pid = int(outcome.text)
    assert_pid_gone(pid)

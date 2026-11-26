"""Shared rig, fakes and helpers for the confirm flow QA cases."""

import asyncio
import json
from typing import Any

import httpx
from fake_endpoint import make_client
from fakes import ENV, SPEECH, Endpoint, tts_wav
from test_confirm_e2e import DeviceSocket, FakeSleep
from test_mcp_source import FakeConnection

from ota_server.hub.hub import McpHub
from ota_server.hub.types import CallCtx, ToolCall, ToolOutcome
from ota_server.mcp.config import McpServerConfig
from ota_server.mcp.source import McpSource
from ota_server.pipeline import VoicePipeline
from ota_server.protocol import MAX_CREDIT
from ota_server.session import DeviceSession

PIN = "sha256:" + "0" * 64
MAIL = ("n8n__send_mail", {"to": "a@b.com"})
LIST: tuple[str, dict[str, Any]] = ("n8n__list_mail", {})
VOLUME = ("device__set_volume", {"level": 80})
Call = tuple[str, dict[str, Any]]


def tool_sse(calls: list[Call]) -> str:
    deltas = [
        {"index": i, "id": f"call_{i}", "function": {"name": n, "arguments": json.dumps(a)}}
        for i, (n, a) in enumerate(calls)
    ]
    line = "data: " + json.dumps({"choices": [{"delta": {"tool_calls": deltas}}]})
    return f"{line}\n\ndata: [DONE]\n\n"


class ScriptEndpoint(Endpoint):
    """STT returns the next transcript (None = failure); chat n runs scripts[n]: a list of
    calls or a text; a spent script gives "Okay."."""

    def __init__(self, transcripts: list[str | None], scripts: list[Any]) -> None:
        super().__init__(reply="Okay.", tts=tts_wav(0.05))
        self.transcripts, self.scripts = transcripts, scripts

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/audio/transcriptions"):
            self.calls.append("transcriptions")
            text = self.transcripts.pop(0)
            if text is None:
                return httpx.Response(500)
            return httpx.Response(200, json={"text": text})
        if not path.endswith("/chat/completions"):
            return super().__call__(req)
        self.calls.append("completions")
        self.chats.append(json.loads(req.content)["messages"])
        script = self.scripts.pop(0) if self.scripts else "Okay."
        if isinstance(script, str):
            return httpx.Response(200, text=_text(script))
        return httpx.Response(200, text=tool_sse(script))


def _text(text: str) -> str:
    chunk = json.dumps({"choices": [{"delta": {"content": text}}]})
    return f"data: {chunk}\n\ndata: [DONE]\n\n"


class Rig:
    """A session, a pipeline, a fake device and an n8n MCP source (fake connection)."""

    def __init__(
        self,
        scripts: list[Any],
        transcripts: list[str | None],
        language: str = "en",
        no_confirm: list[str] | None = None,
        tools: tuple[str, ...] = ("send_mail", "list_mail"),
        timeout: float = 5.0,
        conn: FakeConnection | None = None,
    ) -> None:
        self.endpoint = ScriptEndpoint(["do it", *transcripts], scripts)
        self.conn = conn or FakeConnection(list(tools))
        config = McpServerConfig(
            name="n8n",
            transport="http",
            url="https://h/mcp",
            timeout=timeout,
            tools=dict.fromkeys(tools, PIN),
            no_confirm=no_confirm or [],
        )
        self.source = asyncio.run(McpSource.build(config, self.conn))
        self.socket, self.sleep = DeviceSocket(), FakeSleep()
        pipeline = VoicePipeline(make_client(self.endpoint, ENV), language, "voice-1")
        self.session = DeviceSession(
            self.socket,  # type: ignore[arg-type]
            pipeline,
            mcp_sources=[self.source],
            language=language,
            sleep=self.sleep,
        )
        self.socket.session = self.session

    async def turn(self) -> None:
        session = self.session
        await session.handle_text({"type": "listen.start"})
        session.credit = MAX_CREDIT
        session.audio.extend(SPEECH)
        await session.handle_text({"type": "listen.stop", "turn": session.turn})
        assert session.reply_task is not None
        await session.reply_task
        await asyncio.gather(*self.socket.tasks)
        await asyncio.sleep(0)

    async def turns(self, count: int) -> None:
        for _ in range(count):
            await self.turn()

    async def time_out(self) -> None:
        self.sleep.passed.set()
        assert self.session.confirm_timer is not None
        await self.session.confirm_timer

    def device_calls(self) -> list[dict[str, Any]]:
        return [m for m in self.socket.json() if m["type"] == "tool.call"]

    def types(self) -> list[str]:
        return [m["type"] for m in self.socket.json()]

    def spoken(self) -> list[str]:
        return self.endpoint.spoken

    def chats(self) -> int:
        return len(self.endpoint.chats)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def device_rig(transcripts: list[str | None], language: str = "en") -> Rig:
    return Rig([[VOLUME]], transcripts, language)


def mcp_rig(transcripts: list[str | None], **kw: Any) -> Rig:
    return Rig([[MAIL]], transcripts, **kw)


def n8n(no_confirm: list[str] | None = None, conn: FakeConnection | None = None) -> Any:
    conn = conn or FakeConnection(["send_mail", "list_mail"])
    config = McpServerConfig(
        name="n8n",
        transport="http",
        url="https://h/mcp",
        tools=dict.fromkeys(["send_mail", "list_mail"], PIN),
        no_confirm=no_confirm or [],
    )
    return asyncio.run(McpSource.build(config, conn)), conn


def ask(hub: McpHub, call: Call, ctx: CallCtx, call_id: str = "c1") -> ToolOutcome:
    return asyncio.run(hub.run(ToolCall(call_id, call[0], call[1]), ctx))

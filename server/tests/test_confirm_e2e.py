"""Confirm flow end to end (T10): DeviceSession + VoicePipeline + McpHub + a real
DeviceSource and a fake MCP source. A fake endpoint plays STT, chat and TTS;
a fake socket plays the device; a fake sleep plays the 10 s clock. No board, no real API."""

import asyncio
import json
from typing import Any

import httpx
import pytest
from fake_endpoint import make_client
from fakes import ENV, SPEECH, Endpoint, ListSocket, sse, tts_wav

from ota_server.hub.types import ToolOutcome, ToolSpec
from ota_server.pipeline import VoicePipeline
from ota_server.protocol import MAX_CREDIT
from ota_server.session import DeviceSession

VOLUME = ("device__set_volume", '{"level": 30}')
SWITCH = ("home__switch", '{"room": "kitchen"}')
CALLS = {"device": VOLUME, "mcp": SWITCH}


def tool_sse(name: str, arguments: str) -> str:
    call = {"index": 0, "id": "call_0", "function": {"name": name, "arguments": arguments}}
    line = "data: " + json.dumps({"choices": [{"delta": {"tool_calls": [call]}}]})
    return f"{line}\n\ndata: [DONE]\n\n"


class ConfirmEndpoint(Endpoint):
    """STT returns the next transcript; the first chat asks for `call`, later chats talk.
    Every TTS input is kept, so a test reads what the device was told."""

    def __init__(self, transcripts: list[str], call: tuple[str, str]) -> None:
        super().__init__(reply="Okay.", tts=tts_wav(0.05))
        self.transcripts, self.call = transcripts, call

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/audio/transcriptions"):
            self.calls.append("transcriptions")
            return httpx.Response(200, json={"text": self.transcripts.pop(0)})
        if not path.endswith("/chat/completions"):
            return super().__call__(req)
        self.calls.append("completions")
        self.chats.append(json.loads(req.content)["messages"])
        if len(self.chats) == 1:
            return httpx.Response(200, text=tool_sse(*self.call))
        return httpx.Response(200, text=sse(self.reply))


class DeviceSocket(ListSocket):
    """The fake device: answers each tool.call with an ok tool.result."""

    def __init__(self) -> None:
        super().__init__()
        self.session: DeviceSession | None = None
        self.tasks: list[asyncio.Task[None]] = []

    async def send_json(self, msg: dict[str, Any]) -> None:
        await super().send_json(msg)
        if msg["type"] == "tool.call":
            self.tasks.append(asyncio.create_task(self.answer(msg["id"])))

    async def answer(self, call_id: int) -> None:
        assert self.session is not None
        frame = {"type": "tool.result", "id": call_id, "ok": True, "result": "done"}
        await self.session.handle_text(frame)

    def json(self) -> list[dict[str, Any]]:
        return [m for m in self if isinstance(m, dict)]

    def codes(self) -> list[str]:
        return [m["code"] for m in self.json() if m["type"] == "error"]


class Home:
    """A fake MCP source with one confirm=true tool that counts its runs."""

    name = "home"
    timeout = 1.0

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def specs(self) -> list[ToolSpec]:
        schema = {"type": "object", "properties": {"room": {"type": "string"}}}
        return [ToolSpec(self.name, "switch", "Switch the lamp.", schema, confirm=True)]

    async def call(self, tool: str, args: dict[str, Any]) -> ToolOutcome:
        self.calls.append(args)
        return ToolOutcome(True, "ok")


class FakeSleep:
    """The confirm clock: records each delay and waits until the test lets it pass."""

    def __init__(self) -> None:
        self.delays: list[float] = []
        self.passed = asyncio.Event()

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)
        await self.passed.wait()


class Rig:
    """One session wired to the fakes."""

    def __init__(self, kind: str, transcripts: list[str], language: str = "en") -> None:
        self.kind = kind
        self.endpoint = ConfirmEndpoint(["turn the lamp on", *transcripts], CALLS[kind])
        self.socket, self.sleep, self.home = DeviceSocket(), FakeSleep(), Home()
        pipeline = VoicePipeline(make_client(self.endpoint, ENV), language, "voice-1")
        self.session = DeviceSession(
            self.socket,  # type: ignore[arg-type]
            pipeline,
            mcp_sources=[self.home],
            language=language,
            sleep=self.sleep,
        )
        self.socket.session = self.session

    async def turn(self) -> None:
        """listen.start, the audio, listen.stop, the whole reply and any device answer."""
        session = self.session
        await session.handle_text({"type": "listen.start"})
        session.credit = MAX_CREDIT
        session.audio.extend(SPEECH)
        await session.handle_text({"type": "listen.stop", "turn": session.turn})
        assert session.reply_task is not None
        await session.reply_task
        await asyncio.gather(*self.socket.tasks)
        await asyncio.sleep(0)  # the timer task starts

    async def turns(self, count: int) -> None:
        for _ in range(count):
            await self.turn()

    async def time_out(self) -> None:
        self.sleep.passed.set()
        assert self.session.confirm_timer is not None
        await self.session.confirm_timer

    def calls(self) -> int:
        """Runs of the asked tool at its source."""
        if self.kind == "mcp":
            return len(self.home.calls)
        return sum(1 for m in self.socket.json() if m["type"] == "tool.call")


@pytest.mark.parametrize("kind", ["device", "mcp"])
def test_nothing_runs_before_the_confirmation_turn(kind: str) -> None:
    rig = Rig(kind, [])
    asyncio.run(rig.turns(1))
    assert rig.calls() == 0
    assert "confirm.request" in [m["type"] for m in rig.socket.json()]


@pytest.mark.parametrize("kind", ["device", "mcp"])
@pytest.mark.parametrize(
    ("language", "word"), [("en", "yes"), ("en", "yeah"), ("pl", "tak"), ("uk", "так")]
)
def test_a_yes_runs_the_call_once(kind: str, language: str, word: str) -> None:
    rig = Rig(kind, [word, word], language)

    async def scenario() -> list[int]:
        await rig.turn()
        await rig.turn()
        after_yes = rig.calls()
        await rig.turn()
        return [after_yes, rig.calls()]

    assert asyncio.run(scenario()) == [1, 1]
    assert len(rig.endpoint.chats) == 2  # the confirmation turn made no chat call


@pytest.mark.parametrize("kind", ["device", "mcp"])
@pytest.mark.parametrize("text", ["no", "yes but later", "tak"])
def test_a_non_yes_cancels(kind: str, text: str) -> None:
    rig = Rig(kind, [text])
    asyncio.run(rig.turns(2))
    assert rig.calls() == 0
    assert rig.endpoint.spoken[-1] == "Cancelled."
    assert rig.session.hub.gate.holding() is None


@pytest.mark.parametrize("kind", ["device", "mcp"])
def test_an_empty_transcript_cancels(kind: str) -> None:
    """E6: STT hears nothing, so the turn ends with no_speech and the call is gone."""
    rig = Rig(kind, ["", "yes"])
    asyncio.run(rig.turns(3))
    assert rig.calls() == 0
    assert rig.socket.codes() == ["no_speech"]


@pytest.mark.parametrize("kind", ["device", "mcp"])
def test_timeout(kind: str) -> None:
    rig = Rig(kind, ["yes"])

    async def scenario() -> None:
        await rig.turn()
        await rig.time_out()
        await rig.turn()

    asyncio.run(scenario())
    assert rig.sleep.delays == [10]
    assert {"type": "error", "turn": 1, "code": "confirm_timeout"} in rig.socket.json()
    assert rig.calls() == 0


@pytest.mark.parametrize("kind", ["device", "mcp"])
def test_message_order(kind: str) -> None:
    rig = Rig(kind, [])
    asyncio.run(rig.turns(1))
    msgs = rig.socket.json()
    end = next(i for i, m in enumerate(msgs) if m["type"] == "reply.end")
    assert msgs[end] == {"type": "reply.end", "turn": 1, "reason": "done"}
    assert msgs[end + 1 :] == [
        {"type": "state", "turn": 1, "state": "confirm"},
        {"type": "confirm.request", "turn": 1, "tool": CALLS[kind][0], "timeout_ms": 10000},
    ]

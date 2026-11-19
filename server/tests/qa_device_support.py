"""Shared fakes for the device-tools QA tests (docs/design/device-tools/qa/cases.md).

A fake chat endpoint follows a script of rounds, a fake socket plays the device."""

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
from fakes import Endpoint, tts_wav
from test_device_tools_e2e import (
    DeviceSocket,
    Recorder,
    ends,
    new_session,
    run_turn,
    tool_calls,
    tool_messages,
)

from ota_server.hub.types import ToolOutcome

Call = tuple[str, str]  # full tool name, JSON arguments
Round = tuple[str, list[Call]]  # text streamed first, then the tool calls
Answer = Callable[[int], list[dict[str, Any]]]

VOL = ("device__set_volume", '{"level": 30}')
STATUS = ("device__get_status", "{}")
TEXT: Round = ("Done.", [])
TOOL_ROUND: Round = ("", [VOL])


def delta(payload: dict[str, Any]) -> str:
    return "data: " + json.dumps({"choices": [{"delta": payload}]}) + "\n\n"


def round_sse(text: str, calls: list[Call]) -> str:
    out = delta({"content": text}) if text else ""
    for index, (name, args) in enumerate(calls):
        call = {"index": index, "id": f"c{index}", "function": {"name": name, "arguments": args}}
        out += delta({"tool_calls": [call]})
    return out + "data: [DONE]\n\n"


class QAEndpoint(Endpoint):
    """Chat answers round n of the script (the last repeats); records bodies and TTS input."""

    def __init__(
        self,
        rounds: list[Round],
        raw: dict[int, str] | None = None,
        reject_tools: bool = False,
    ) -> None:
        super().__init__(tts=tts_wav(0.05))
        self.rounds, self.raw, self.reject_tools = rounds, raw or {}, reject_tools
        self.bodies: list[dict[str, Any]] = []
        self.spoken: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/audio/speech"):
            self.spoken.append(json.loads(req.content)["input"])
        if not path.endswith("/chat/completions"):
            return super().__call__(req)
        body = json.loads(req.content)
        self.calls.append("completions")
        self.bodies.append(body)
        self.chats.append(body["messages"])
        n = len(self.bodies) - 1
        if self.reject_tools and "tools" in body:
            return httpx.Response(400)
        if n in self.raw:
            return httpx.Response(200, text=self.raw[n])
        text, calls = self.rounds[min(n, len(self.rounds) - 1)]
        return httpx.Response(200, text=round_sse(text, calls))


class SlowDevice(DeviceSocket):
    def __init__(self, answer: Answer, delay: float) -> None:
        super().__init__(answer)
        self.delay = delay

    async def reply_to(self, call_id: int) -> None:
        await asyncio.sleep(self.delay)
        await super().reply_to(call_id)


def result(ok: bool = True, **fields: Any) -> Answer:
    def answer(call_id: int) -> list[dict[str, Any]]:
        return [{"type": "tool.result", "id": call_id, "ok": ok, **fields}]

    return answer


def raw_result(**fields: Any) -> Answer:
    def answer(call_id: int) -> list[dict[str, Any]]:
        return [{"type": "tool.result", "id": call_id, **fields}]

    return answer


class Run:
    """One session with a recorder around each turn's tools."""

    def __init__(self, rounds: list[Round], answer: Answer | None = None, **kw: Any) -> None:
        self.endpoint = QAEndpoint(rounds, **kw)
        self.socket = DeviceSocket(answer or result(result="ok"))
        self.session = new_session(self.endpoint, self.socket)
        self.outcomes: list[tuple[ToolOutcome, float]] = []
        real = self.session.turn_tools
        self.session.turn_tools = lambda: Recorder(real(), self.outcomes)  # type: ignore[method-assign]

    @property
    def codes(self) -> list[str | None]:
        return [outcome.code for outcome, _ in self.outcomes]

    @property
    def heard(self) -> str:
        return " ".join(self.endpoint.spoken)

    def go(self) -> "Run":
        asyncio.run(run_turn(self.session))
        return self

    def errors(self) -> list[str]:
        return [m["code"] for m in self.socket if isinstance(m, dict) and m["type"] == "error"]


def refused(call: Call, code: str) -> None:
    run = Run([("", [call]), TEXT]).go()
    assert run.codes == [code]
    assert not tool_calls(run.socket)
    assert len(tool_messages(run.endpoint.chats[1])) == 1
    assert ends(run.socket)[-1]["reason"] == "done"
    assert run.heard == "Done."


def limit_texts(run: Run, request: int, start: int) -> list[str]:
    return tool_messages(run.endpoint.chats[request])[start:]

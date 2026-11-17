"""Shared test fakes: device audio, a fake OpenAI-compatible endpoint, a list socket."""

import io
import json
import random
import wave
from array import array
from collections.abc import Callable
from typing import Any

import httpx
from fake_endpoint import make_client
from model_env import ENDPOINTS

from ota_server.pipeline import SELF_TEST, VoicePipeline
from ota_server.protocol import BYTES_PER_SECOND, SAMPLE_RATE
from ota_server.providers import MISTRAL_CORE, OPENAI_CORE

ENV = {
    **ENDPOINTS,
    "OTA_MODERATION_MODEL": "mod-m",
    "OTA_CHAT_RATE": "100",
    "OTA_STT_RATE": "100",
    "OTA_TTS_RATE": "100",
    "OTA_MODERATION_RATE": "100",
}
CLEAN = {name: False for name in [*MISTRAL_CORE, "health", "pii"]}  # Mistral schema
OPENAI_CLEAN = {name: False for name in OPENAI_CORE}
SELF_TEST_WORD = "zabić"  # in the harmful Polish self-test phrase
CANARY = SELF_TEST["pl"][0]  # rides along in every moderation batch


def square(seconds: float, amplitude: int) -> bytes:
    """Device PCM: a 200 Hz square wave, speech level for the VAD when loud enough."""
    period = SAMPLE_RATE // 200
    wave_ = [amplitude if i % period < period // 2 else -amplitude for i in range(period)]
    return array("h", wave_ * int(seconds * 200)).tobytes()


SPEECH = square(1, 3000)  # 1 s of device PCM above the VAD threshold
SILENCE = b"\x00\x00" * BYTES_PER_SECOND  # 1 s


def tts_wav(seconds: float = 0.5, rate: int = 24000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(array("h", [300] * int(seconds * rate)).tobytes())
    return out.getvalue()


def sse(text: str) -> str:
    chunk = json.dumps({"choices": [{"delta": {"content": text}}]})
    return f"data: {chunk}\n\ndata: [DONE]\n\n"


def self_test_only(text: str) -> bool:
    return SELF_TEST_WORD in text


class Endpoint:
    """A fake OpenAI-compatible endpoint. `flag(text)` decides each moderation verdict.
    Moderation calls are numbered: 1 is the self-test, 2 the transcript, 3 the reply.
    `result`, `moderation_status` and `result_count_delta` apply from call `from_call` on,
    so a retried 429 or a repeated self-test meets the same answer."""

    def __init__(
        self,
        transcript: str = "Jak się masz?",
        reply: str = "Dobrze. A ty?",
        tts: bytes | None = None,
        fail: dict[str, int] | None = None,
        flag: Callable[[str], bool] = self_test_only,
        result: dict[str, Any] | None = None,
        moderation_status: int | None = None,
        result_count_delta: int = 0,
        from_call: int = 2,
    ) -> None:
        self.transcript, self.reply, self.tts, self.fail = transcript, reply, tts, fail or {}
        self.flag, self.result, self.moderation_status = flag, result, moderation_status
        self.result_count_delta, self.from_call = result_count_delta, from_call
        self.calls: list[str] = []
        self.moderated: list[list[str]] = []  # the inputs of each moderation call
        self.chats: list[list[dict[str, Any]]] = []  # the messages of each chat call

    def verdict(self, text: str) -> dict[str, Any]:
        return {"categories": {**CLEAN, "selfharm": self.flag(text)}}

    def moderation(self, req: httpx.Request) -> httpx.Response:
        texts = json.loads(req.content)["input"]
        self.moderated.append(texts)
        aimed = len(self.moderated) >= self.from_call
        if aimed and self.moderation_status:
            return httpx.Response(self.moderation_status)
        results = [self.verdict(t) for t in texts]
        if aimed and self.result is not None:  # the canary keeps its true verdict
            results = [
                r if self_test_only(t) else self.result for t, r in zip(texts, results, strict=True)
            ]
        if aimed and self.result_count_delta < 0:
            results = results[: self.result_count_delta]
        if aimed and self.result_count_delta > 0:
            results += results[:1] * self.result_count_delta
        return httpx.Response(200, json={"results": results})

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        self.calls.append(path.rsplit("/", 1)[-1])
        if path in self.fail:
            return httpx.Response(self.fail[path])
        if path.endswith("/moderations"):
            return self.moderation(req)
        if path.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": self.transcript})
        if path.endswith("/chat/completions"):
            self.chats.append(json.loads(req.content)["messages"])
            return httpx.Response(200, text=sse(self.reply))
        return httpx.Response(200, content=self.tts if self.tts is not None else tts_wav())


def pipeline_for(endpoint: Endpoint) -> VoicePipeline:
    return VoicePipeline(make_client(endpoint, ENV), "pl", "voice-1")


def silence(ms: int) -> bytes:
    return bytes(SAMPLE_RATE * ms // 1000 * 2)


def tone(ms: int, amplitude: int = 8000) -> bytes:
    return square(ms / 1000, amplitude)


def noise(ms: int, amplitude: int = 300) -> bytes:
    rng = random.Random(1)
    n = SAMPLE_RATE * ms // 1000
    return array("h", [rng.randint(-amplitude, amplitude) for _ in range(n)]).tobytes()


class ListSocket(list[Any]):
    """A fake WebSocket that keeps every sent message in order."""

    async def send_json(self, msg: dict[str, Any]) -> None:
        self.append(msg)

    async def send_bytes(self, data: bytes) -> None:
        self.append(data)

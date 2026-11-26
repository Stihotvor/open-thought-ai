"""Shared test fakes: device audio, a fake OpenAI-compatible endpoint, a list socket."""

import io
import json
import random
import wave
from array import array
from typing import Any

import httpx
from fake_endpoint import make_client
from model_env import ENDPOINTS

from ota_server.pipeline import VoicePipeline
from ota_server.protocol import BYTES_PER_SECOND, SAMPLE_RATE

ENV = {
    **ENDPOINTS,
    "OTA_CHAT_RATE": "100",
    "OTA_STT_RATE": "100",
    "OTA_TTS_RATE": "100",
}


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


class Endpoint:
    """A fake OpenAI-compatible endpoint for STT, chat and TTS. `fail` maps a path to the
    HTTP status it answers with."""

    def __init__(
        self,
        transcript: str = "Jak się masz?",
        reply: str = "Dobrze. A ty?",
        tts: bytes | None = None,
        fail: dict[str, int] | None = None,
    ) -> None:
        self.transcript, self.reply, self.tts, self.fail = transcript, reply, tts, fail or {}
        self.calls: list[str] = []
        self.chats: list[list[dict[str, Any]]] = []  # the messages of each chat call
        self.spoken: list[str] = []  # the input of each TTS call

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        self.calls.append(path.rsplit("/", 1)[-1])
        if path in self.fail:
            return httpx.Response(self.fail[path])
        if path.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": self.transcript})
        if path.endswith("/chat/completions"):
            self.chats.append(json.loads(req.content)["messages"])
            return httpx.Response(200, text=sse(self.reply))
        self.spoken.append(json.loads(req.content)["input"])
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

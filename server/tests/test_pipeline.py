import asyncio
import io
import json
import wave
from array import array

import httpx
import pytest
from fake_endpoint import make_client
from model_env import ENDPOINTS

from ota_server import pipeline as pipeline_module
from ota_server.pipeline import TTS_CHUNK, VoicePipeline, system_prompt, tts_chunks
from ota_server.protocol import BYTES_PER_SECOND, TurnError
from ota_server.providers import MAX_TTS_TEXT

ENV = {**ENDPOINTS, "OTA_CHAT_RATE": "100", "OTA_STT_RATE": "100", "OTA_TTS_RATE": "100"}
SPEECH = b"\x01\x00" * BYTES_PER_SECOND  # 1 s of device PCM


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


def endpoint(transcript="Jak się masz?", reply="Dobrze. A ty?", tts=None, fail=None):
    """Routes requests by path. `fail` maps a path to a status code to return instead."""
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        calls.append(path)
        if fail and path in fail:
            return httpx.Response(fail[path])
        if path.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": transcript})
        if path.endswith("/chat/completions"):
            return httpx.Response(200, text=sse(reply))
        return httpx.Response(200, content=tts if tts is not None else tts_wav())

    return handler, calls


def run(handler):
    pipeline = VoicePipeline(make_client(handler, ENV), "pl", "voice-1")
    return asyncio.run(pipeline(SPEECH))


def test_turn_runs_stt_chat_tts_and_returns_device_pcm():
    handler, calls = endpoint()
    pcm = run(handler)
    assert [c.rsplit("/", 2)[-1] for c in calls] == [
        "transcriptions",
        "completions",
        "speech",
        "speech",
    ]
    assert len(pcm) == BYTES_PER_SECOND  # two TTS chunks of 0.5 s each, now at 16 kHz


def test_empty_transcript_is_no_speech():
    handler, _ = endpoint(transcript="  ")
    with pytest.raises(TurnError) as err:
        run(handler)
    assert err.value.code == "no_speech"


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/v1/audio/transcriptions", 500, "stt_failed"),
        ("/v1/chat/completions", 500, "llm_failed"),
        ("/v1/audio/speech", 500, "tts_failed"),
        ("/v1/audio/transcriptions", 429, "rate_limited"),
    ],
)
def test_failures_map_to_device_error_codes(path, status, code):
    handler, _ = endpoint(fail={path: status})
    with pytest.raises(TurnError) as err:
        run(handler)
    assert err.value.code == code


def test_unplayable_tts_audio_is_tts_failed():
    handler, _ = endpoint(tts=b"not a wav")
    with pytest.raises(TurnError) as err:
        run(handler)
    assert err.value.code == "tts_failed"


def test_empty_reply_is_llm_failed():
    handler, _ = endpoint(reply=" ")
    with pytest.raises(TurnError) as err:
        run(handler)
    assert err.value.code == "llm_failed"


def test_first_sentence_alone_then_merged():
    text = "Hi. One. Two. Three."
    assert tts_chunks(text) == ["Hi.", "One. Two. Three."]


def test_short_sentences_are_merged_up_to_the_chunk_size():
    s = "a" * 49 + "."  # 50 characters
    chunks = tts_chunks(" ".join([s] * 7))
    # first alone; then 5 sentences fit in TTS_CHUNK (254 chars), the 6th would not (305)
    assert chunks == [s, " ".join([s] * 5), s]
    assert all(len(c) <= TTS_CHUNK for c in chunks)


def test_very_long_sentence_is_cut_for_tts():
    chunks = tts_chunks("word " * 500)
    assert all(len(c) <= MAX_TTS_TEXT for c in chunks)
    assert " ".join(chunks).split() == ["word"] * 500


def test_tts_stops_once_the_cap_is_reached(monkeypatch):
    monkeypatch.setattr(pipeline_module, "SPEECH_CAP", BYTES_PER_SECOND // 2)  # 0.5 s
    handler, calls = endpoint(reply="Hi. Two. Three.")
    run(handler)
    assert sum(c.endswith("/audio/speech") for c in calls) == 1


def test_prompt_names_the_language():
    assert "Ukrainian" in system_prompt("uk")

import asyncio
import json
import logging

import httpx
import pytest

from ota_server.providers import ConfigError, ModelClient, ProviderError, load_roles, make_buckets
from ota_server.ratelimit import RateLimited, TurnBudget

BASE = {
    "OTA_MODEL_BASE_URL": "https://api.example.test/v1",
    "OTA_MODEL_KEY": "default-key",
    "OTA_CHAT_MODEL": "chat-m",
    "OTA_STT_MODEL": "stt-m",
    "OTA_TTS_MODEL": "tts-m",
    "OTA_CHAT_RATE": "100",
    "OTA_STT_RATE": "100",
    "OTA_TTS_RATE": "100",
}


def env(**extra):
    return {**BASE, **extra}


# --- config rules ---


def test_unset_role_is_skipped_and_default_endpoint_used():
    roles = load_roles(BASE)
    assert set(roles) == {"chat", "stt", "tts"}
    assert roles["stt"].base_url == "https://api.example.test/v1"
    assert roles["stt"].key == "default-key"


def test_own_base_url_without_own_key_fails():
    with pytest.raises(ConfigError, match="own key"):
        load_roles(env(OTA_STT_BASE_URL="https://stt.example.test/v1"))


def test_own_base_url_with_own_key():
    roles = load_roles(env(OTA_STT_BASE_URL="https://stt.example.test/v1", OTA_STT_KEY="k2"))
    assert roles["stt"].key == "k2"
    assert roles["chat"].key == "default-key"


def test_http_on_lan_needs_flag():
    lan = env(OTA_STT_BASE_URL="http://192.168.1.5:8000/v1", OTA_STT_KEY="k")
    with pytest.raises(ConfigError, match="http://"):
        load_roles(lan)
    assert load_roles({**lan, "OTA_ALLOW_INSECURE_MODEL_ENDPOINT": "1"})["stt"]


def test_http_on_loopback_is_allowed():
    roles = load_roles(env(OTA_TTS_BASE_URL="http://127.0.0.1:5000/v1", OTA_TTS_KEY="k"))
    assert roles["tts"].host == "127.0.0.1"


def test_credentials_in_url_are_rejected():
    with pytest.raises(ConfigError, match="credentials"):
        load_roles(env(OTA_MODEL_BASE_URL="https://user:pw@api.example.test/v1"))


def test_moderation_gets_own_bucket():
    roles = load_roles(env(OTA_MODERATION_MODEL="mod-m"))
    buckets = make_buckets(roles)
    assert buckets["chat"] is buckets["stt"]  # same endpoint and key share one bucket
    assert buckets["moderation"] is not buckets["chat"]


# --- calls against a fake endpoint ---


def client_with(handler):
    roles = load_roles(BASE)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ModelClient(roles, http)


def test_transcribe_sends_key_only_to_its_endpoint():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.host, req.headers["authorization"]))
        return httpx.Response(200, json={"text": "dzień dobry"})

    text = asyncio.run(client_with(handler).transcribe(b"RIFF", "pl", TurnBudget()))
    assert text == "dzień dobry"
    assert seen == [("api.example.test", "Bearer default-key")]


def test_speak_returns_audio():
    def handler(req):
        return httpx.Response(200, content=b"WAVDATA")

    assert asyncio.run(client_with(handler).speak("hi", "v", TurnBudget())) == b"WAVDATA"


def test_429_is_retried_then_succeeds(caplog):
    replies = [
        httpx.Response(429, headers={"retry-after": "0"}),
        httpx.Response(200, json={"text": "ok"}),
    ]

    def handler(req):
        return replies.pop(0)

    with caplog.at_level(logging.WARNING):
        text = asyncio.run(client_with(handler).transcribe(b"x", "en", TurnBudget()))
    assert text == "ok"
    assert "default-key" not in caplog.text
    assert "429 role=stt retry=0 host=api.example.test" in caplog.text


def test_persistent_429_ends_with_rate_limited():
    def handler(req):
        return httpx.Response(429, headers={"retry-after": "0"})

    with pytest.raises(RateLimited):
        asyncio.run(client_with(handler).transcribe(b"x", "en", TurnBudget()))


def test_server_error_is_not_retried_and_body_not_logged(caplog):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(500, text="secret echo default-key")

    with caplog.at_level(logging.WARNING), pytest.raises(ProviderError):
        asyncio.run(client_with(handler).transcribe(b"x", "en", TurnBudget()))
    assert len(calls) == 1
    assert "secret echo" not in caplog.text


def sse(*deltas):
    lines = ["data: " + json.dumps({"choices": [{"delta": {"content": d}}]}) for d in deltas] + [
        "data: [DONE]"
    ]
    return "\n\n".join(lines) + "\n\n"


async def collect(gen):
    return [x async for x in gen]


def test_chat_streams_deltas_after_429():
    replies = [
        httpx.Response(429, headers={"retry-after": "0"}),
        httpx.Response(200, text=sse("Hel", "lo")),
    ]

    def handler(req):
        return replies.pop(0)

    client = client_with(handler)
    out = asyncio.run(collect(client.chat([{"role": "user", "content": "hi"}], TurnBudget())))
    assert out == ["Hel", "lo"]

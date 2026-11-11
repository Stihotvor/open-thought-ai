import asyncio
import contextlib
import inspect
import json
import logging

import httpx
import pytest

from ota_server.endpoints import ConfigError, load_roles
from ota_server.providers import (
    MAX_CHAT_TEXT,
    ModelClient,
    ProviderError,
    make_buckets,
    make_http_client,
    new_turn_budget,
)
from ota_server.ratelimit import CallCapExceeded, DeadlineExceeded, RateLimited, TurnBudget

BASE = {
    "OTA_MODEL_BASE_URL": "https://api.example.test/v1",
    "OTA_MODEL_KEY": "default-key",
    "OTA_CHAT_MODEL": "chat-m",
    "OTA_STT_MODEL": "stt-m",
    "OTA_TTS_MODEL": "tts-m",
    "OTA_MODERATION_MODEL": "mod-m",
    "OTA_CHAT_RATE": "100",
    "OTA_STT_RATE": "100",
    "OTA_TTS_RATE": "100",
    "OTA_MODERATION_RATE": "100",
}


def as_network(resp: httpx.Response) -> httpx.Response:
    """MockTransport hands back a response that is already read. A real one is not, and
    the client reads raw bytes, so rebuild it with an unread stream."""
    if not isinstance(resp.stream, httpx.ByteStream):
        return resp  # a custom test stream, already unread
    return httpx.Response(
        resp.status_code, headers=resp.headers, stream=httpx.ByteStream(resp.content)
    )


def client_with(handler, env=BASE, **http_kw):
    async def network(req):
        resp = handler(req)
        if inspect.isawaitable(resp):
            resp = await resp
        return as_network(resp)

    http = httpx.AsyncClient(transport=httpx.MockTransport(network), **http_kw)
    return ModelClient(load_roles(env), http)


def sse(*deltas):
    lines = ["data: " + json.dumps({"choices": [{"delta": {"content": d}}]}) for d in deltas]
    return "\n\n".join([*lines, "data: [DONE]"]) + "\n\n"


async def collect(client, budget=None):
    stream = client.chat([{"role": "user", "content": "hi"}], budget or new_turn_budget())
    async with contextlib.aclosing(stream) as deltas:
        return [d async for d in deltas]


def replies(*items):
    queue = list(items)
    return lambda req: queue.pop(0)


def rate_limit_reply():
    return httpx.Response(429, headers={"retry-after": "0"})


# --- wiring ---


def test_moderation_gets_own_bucket():
    buckets = make_buckets(load_roles(BASE))
    assert buckets["chat"] is buckets["stt"]
    assert buckets["moderation"] is not buckets["chat"]


def test_shared_endpoint_needs_same_limits():
    with pytest.raises(ConfigError, match="same rate"):
        make_buckets(load_roles({**BASE, "OTA_STT_RATE": "5"}))


def test_unconfigured_role_is_provider_error():
    env = {k: v for k, v in BASE.items() if not k.startswith("OTA_TTS")}
    client = client_with(lambda req: httpx.Response(200), env)
    with pytest.raises(ProviderError) as err:
        asyncio.run(client.speak("hi", "v", new_turn_budget()))
    assert err.value.role == "tts"


def test_requests_ask_for_identity_encoding():
    seen = []

    def handler(req):
        seen.append(req.headers["accept-encoding"])
        return httpx.Response(200, content=b"WAV")

    asyncio.run(client_with(handler).speak("hi", "v", new_turn_budget()))
    assert seen == ["identity"]


def test_compressed_body_is_refused():
    gz = httpx.Response(200, headers={"content-encoding": "gzip"}, content=b"x")
    with pytest.raises(ProviderError):
        asyncio.run(client_with(lambda req: gz).speak("hi", "v", new_turn_budget()))


@pytest.mark.parametrize("payload", [{"text": 5}, {"text": "x" * 5000}])
def test_bad_transcript_is_refused(payload):
    client = client_with(lambda req: httpx.Response(200, json=payload))
    with pytest.raises(ProviderError):
        asyncio.run(client.transcribe(b"x", "en", new_turn_budget()))


def test_shared_client_is_safe():
    http = make_http_client()
    assert not http.follow_redirects
    asyncio.run(http.aclose())


def test_client_refuses_redirect_following():
    with pytest.raises(ValueError, match="redirects"):
        client_with(lambda req: httpx.Response(200), follow_redirects=True)


# --- calls ---


def test_transcribe_sends_key_only_to_its_endpoint():
    seen = []

    def handler(req):
        seen.append((req.url.host, req.headers.get("authorization")))
        return httpx.Response(200, json={"text": "dzień dobry"})

    env = {**BASE, "OTA_STT_BASE_URL": "https://stt.example.test/v1", "OTA_STT_KEY": "stt-key"}
    text = asyncio.run(client_with(handler, env).transcribe(b"RIFF", "pl", new_turn_budget()))
    assert text == "dzień dobry"
    assert seen == [("stt.example.test", "Bearer stt-key")]


def test_speak_returns_audio():
    client = client_with(lambda req: httpx.Response(200, content=b"WAVDATA"))
    assert asyncio.run(client.speak("hi", "v", new_turn_budget())) == b"WAVDATA"


def test_too_long_tts_text_is_refused():
    client = client_with(lambda req: httpx.Response(200))
    with pytest.raises(ProviderError):
        asyncio.run(client.speak("x" * 2000, "v", new_turn_budget()))


def test_429_is_retried_then_succeeds(caplog):
    client = client_with(replies(rate_limit_reply(), httpx.Response(200, json={"text": "ok"})))
    with caplog.at_level(logging.WARNING):
        text = asyncio.run(client.transcribe(b"x", "en", new_turn_budget()))
    assert text == "ok"
    assert "default-key" not in caplog.text
    assert "429 role=stt status=429 retry=0 host=api.example.test" in caplog.text


@pytest.mark.parametrize("value", ["-5", "nan", "abc"])
def test_bad_retry_after_falls_back_to_backoff(value):
    client = client_with(
        replies(
            httpx.Response(429, headers={"retry-after": value}),
            httpx.Response(200, json={"text": "ok"}),
        )
    )
    assert asyncio.run(client.transcribe(b"x", "en", new_turn_budget())) == "ok"


def test_persistent_429_ends_with_rate_limited():
    client = client_with(lambda req: rate_limit_reply())
    with pytest.raises(RateLimited):
        asyncio.run(client.transcribe(b"x", "en", new_turn_budget()))


def test_retry_after_past_deadline_ends_turn_at_once():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "30"})

    with pytest.raises(RateLimited):
        asyncio.run(client_with(handler).transcribe(b"x", "en", new_turn_budget()))
    assert len(calls) == 1


@pytest.mark.parametrize("status", [302, 500])
def test_non_429_errors_are_not_retried_and_body_not_logged(caplog, status):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(status, text="secret echo default-key")

    with caplog.at_level(logging.WARNING), pytest.raises(ProviderError) as err:
        asyncio.run(client_with(handler).transcribe(b"x", "en", new_turn_budget()))
    assert (err.value.role, err.value.status) == ("stt", status)
    assert len(calls) == 1
    assert "secret echo" not in caplog.text


def test_network_error_becomes_provider_error():
    def handler(req):
        raise httpx.ConnectError("down")

    with pytest.raises(ProviderError) as err:
        asyncio.run(client_with(handler).transcribe(b"x", "en", new_turn_budget()))
    assert err.value.role == "stt"


def test_bad_json_becomes_provider_error():
    client = client_with(lambda req: httpx.Response(200, text="not json"))
    with pytest.raises(ProviderError):
        asyncio.run(client.transcribe(b"x", "en", new_turn_budget()))


def test_oversized_body_is_refused():
    big = httpx.Response(200, headers={"content-length": str(10**9)}, content=b"x")
    with pytest.raises(ProviderError):
        asyncio.run(client_with(lambda req: big).speak("hi", "v", new_turn_budget()))


def test_call_cap_applies():
    client = client_with(lambda req: httpx.Response(200, json={"text": "ok"}))
    budget = TurnBudget(max_calls={"stt": 1})
    asyncio.run(client.transcribe(b"x", "en", budget))
    with pytest.raises(CallCapExceeded):
        asyncio.run(client.transcribe(b"x", "en", budget))


def test_slow_call_hits_deadline():
    async def handler(req):
        await asyncio.sleep(1)
        return httpx.Response(200, json={"text": "late"})

    with pytest.raises(DeadlineExceeded):
        asyncio.run(client_with(handler).transcribe(b"x", "en", TurnBudget(deadline_s=0.1)))


# --- chat stream ---


def test_chat_streams_deltas_after_429():
    client = client_with(replies(rate_limit_reply(), httpx.Response(200, text=sse("Hel", "lo"))))
    assert asyncio.run(collect(client)) == ["Hel", "lo"]


def test_chat_skips_empty_choices():
    text = 'data: {"choices": []}\n\n' + sse("ok")
    assert asyncio.run(collect(client_with(lambda req: httpx.Response(200, text=text)))) == ["ok"]


def test_chat_bad_chunk_becomes_provider_error():
    client = client_with(lambda req: httpx.Response(200, text="data: {broken\n\n"))
    with pytest.raises(ProviderError):
        asyncio.run(collect(client))


def test_chat_too_long_reply_is_cut():
    client = client_with(lambda req: httpx.Response(200, text=sse("x" * (MAX_CHAT_TEXT + 1))))
    with pytest.raises(ProviderError):
        asyncio.run(collect(client))


def test_chat_line_without_newline_is_cut():
    client = client_with(lambda req: httpx.Response(200, content=b"data: " + b"x" * 70000))
    with pytest.raises(ProviderError):
        asyncio.run(collect(client))


class SlowStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield ("data: " + json.dumps({"choices": [{"delta": {"content": "a"}}]}) + "\n\n").encode()
        await asyncio.sleep(1)
        yield b"data: [DONE]\n\n"


def test_chat_stream_hits_deadline():
    client = client_with(lambda req: httpx.Response(200, stream=SlowStream()))
    with pytest.raises(DeadlineExceeded):
        asyncio.run(collect(client, TurnBudget(deadline_s=0.2)))

"""Protocol v1 tests with an in-process fake device."""

import asyncio
import hashlib
import json
from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient
from model_env import ENDPOINTS
from starlette.websockets import WebSocketDisconnect

from ota_server import gateway
from ota_server import session as session_module
from ota_server.config import Settings, get_settings
from ota_server.endpoints import ConfigError
from ota_server.main import create_app
from ota_server.pipeline import VoicePipeline
from ota_server.protocol import AUDIO_CHUNK, BYTES_PER_SECOND, Close, TurnError
from ota_server.ratelimit import TokenBucket

TOKEN = "test-token-0123456789abcdef0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client():
    app = create_app()
    digest = hashlib.sha256(TOKEN.encode()).hexdigest()
    settings = Settings(device_id="test", device_token_sha256=digest)
    app.dependency_overrides[get_settings] = lambda: settings
    get_settings.cache_clear()
    gateway.slot = gateway.SessionSlot()  # no session left over from an earlier test
    with TestClient(app) as c, ExitStack() as sockets:
        c.sockets = sockets  # closes every socket that connect() opened, before the app stops
        yield c


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("OTA_DEVICE_ID", "test")
    monkeypatch.setenv("OTA_DEVICE_TOKEN_SHA256", hashlib.sha256(TOKEN.encode()).hexdigest())


def connect(client):
    ws = client.sockets.enter_context(client.websocket_connect("/v1/device", headers=AUTH))
    ws.send_json({"type": "hello", "proto": 1, "fw": "test", "device_id": "test"})
    assert ws.receive_json()["type"] == "hello"
    return ws


def close_code(ws) -> int:
    with pytest.raises(WebSocketDisconnect) as exc:
        ws.receive_text()
    return exc.value.code


def speak(ws, seconds: float) -> int:
    ws.send_json({"type": "listen.start"})
    start = ws.receive_json()
    assert start["type"] == "turn.start"
    turn = start["turn"]
    pcm = b"\x01\x00" * int(seconds * BYTES_PER_SECOND / 2)
    for i in range(0, len(pcm), AUDIO_CHUNK):
        ws.send_bytes(bytes([turn]) + pcm[i : i + AUDIO_CHUNK])
    ws.send_json({"type": "listen.stop", "turn": turn})
    return turn


def test_bad_token_closes_4401(client):
    with client.websocket_connect("/v1/device", headers={"Authorization": "Bearer nope"}) as ws:
        assert close_code(ws) == Close.BAD_TOKEN


def test_wrong_proto_closes_4426(client):
    with client.websocket_connect("/v1/device", headers=AUTH) as ws:
        ws.send_json({"type": "hello", "proto": 2})
        assert close_code(ws) == Close.BAD_VERSION


def test_first_message_must_be_hello(client):
    with client.websocket_connect("/v1/device", headers=AUTH) as ws:
        ws.send_json({"type": "listen.start"})
        assert close_code(ws) == Close.PROTOCOL


def test_oversized_text_frame_closes_4400(client):
    ws = connect(client)
    ws.send_text('{"type":"x","pad":"' + "a" * 2000 + '"}')
    assert close_code(ws) == Close.PROTOCOL


def test_echo_round_trip_with_credit(client):
    ws = connect(client)
    turn = speak(ws, 0.5)
    # Credit sent before reply.start must count. The start credit alone is too small.
    ws.send_json({"type": "audio.credit", "bytes": 16000})
    assert ws.receive_json() == {"type": "state", "turn": turn, "state": "thinking"}
    assert ws.receive_json() == {"type": "state", "turn": turn, "state": "speaking"}
    assert ws.receive_json() == {"type": "reply.start", "turn": turn}
    received = b""
    while True:
        msg = ws.receive()
        if msg.get("bytes"):
            assert msg["bytes"][0] == turn
            received += msg["bytes"][1:]
        else:
            break
    assert len(received) == int(0.5 * BYTES_PER_SECOND)
    assert '"reply.end"' in msg["text"] and '"done"' in msg["text"]


def test_short_audio_gives_no_speech(client):
    ws = connect(client)
    turn = speak(ws, 0.1)
    assert ws.receive_json() == {"type": "error", "turn": turn, "code": "no_speech"}


def test_too_many_turn_starts_are_refused(client):
    gateway.slot.turns = TokenBucket(rate=1 / 60, burst=2)  # 2 turns, then one per minute
    ws = connect(client)
    for _ in range(2):
        ws.send_json({"type": "listen.start"})
        assert ws.receive_json()["type"] == "turn.start"
    ws.send_json({"type": "listen.start"})
    assert ws.receive_json() == {"type": "error", "turn": 2, "code": "too_many_turns"}


def test_turn_limit_survives_reconnect(client):
    gateway.slot.turns = TokenBucket(rate=1 / 60, burst=1)
    first = connect(client)
    first.send_json({"type": "listen.start"})
    assert first.receive_json()["type"] == "turn.start"
    second = connect(client)
    second.send_json({"type": "listen.start"})
    assert second.receive_json()["code"] == "too_many_turns"


def failing_source(error: Exception, chunks: int = 0):
    async def source(pcm: bytes):
        for _ in range(chunks):
            yield pcm[:AUDIO_CHUNK]
        raise error

    return source


@pytest.mark.parametrize(
    ("error", "code"),
    [(TurnError("stt_failed"), "stt_failed"), (RuntimeError("bug"), "internal")],
)
def test_failed_turn_sends_error_code(client, error, code):
    client.app.state.reply_source = failing_source(error)
    ws = connect(client)
    turn = speak(ws, 0.5)
    assert ws.receive_json() == {"type": "state", "turn": turn, "state": "thinking"}
    assert ws.receive_json() == {"type": "error", "turn": turn, "code": code}


VOICE_ENV = {**ENDPOINTS, "OTA_MODERATION_MODEL": "mod-m", "OTA_PIPELINE": "voice"}


def voice_env(monkeypatch, **extra):
    for name, value in {**VOICE_ENV, **extra}.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("extra", "match"),
    [
        ({"OTA_MODERATION_MODEL": ""}, "needs these roles: moderation"),
        ({"OTA_TTS_MODEL": ""}, "needs these roles: tts"),
    ],
)
def test_voice_pipeline_refuses_to_start(monkeypatch, extra, match):
    voice_env(monkeypatch, **extra)
    try:
        with pytest.raises(ConfigError, match=match), TestClient(create_app()):
            pass
    finally:
        get_settings.cache_clear()


def test_voice_pipeline_starts_with_all_roles(monkeypatch):
    voice_env(monkeypatch)
    try:
        app = create_app()
        with TestClient(app):
            assert isinstance(app.state.reply_source, VoicePipeline)
    finally:
        get_settings.cache_clear()


def test_abort_while_thinking_ends_the_reply(client):
    async def slow(pcm: bytes):
        await asyncio.Event().wait()  # never answers
        yield pcm

    client.app.state.reply_source = slow
    ws = connect(client)
    turn = speak(ws, 0.5)
    assert ws.receive_json() == {"type": "state", "turn": turn, "state": "thinking"}
    ws.send_json({"type": "abort", "turn": turn})
    assert ws.receive_json() == {"type": "reply.end", "turn": turn, "reason": "abort"}


def test_long_reply_ends_with_cap(client, monkeypatch):
    monkeypatch.setattr(session_module, "SPEECH_CAP", AUDIO_CHUNK)  # 100 ms for a fast test
    ws = connect(client)
    turn = speak(ws, 0.5)  # echo returns 500 ms
    msgs, received = [], b""
    while True:
        msg = ws.receive()
        if msg.get("bytes"):
            received += msg["bytes"][1:]
        elif msg.get("text"):
            msgs.append(json.loads(msg["text"])["type"])
            if msgs[-1] == "reply.end":
                break
    assert msgs == ["state", "state", "reply.start", "reply.end"]
    assert json.loads(msg["text"]) == {"type": "reply.end", "turn": turn, "reason": "cap"}
    assert len(received) == AUDIO_CHUNK  # cut exactly at the cap


def test_30_s_of_audio_starts_the_reply(client):
    ws = connect(client)
    ws.send_json({"type": "listen.start"})
    turn = ws.receive_json()["turn"]
    chunk = bytes([turn]) + b"\x01\x00" * (AUDIO_CHUNK // 2)
    for _ in range(301):  # 30.1 s: the last frame crosses the limit
        ws.send_bytes(chunk)
    assert ws.receive_json() == {"type": "state", "turn": turn, "state": "thinking"}


def test_new_connection_replaces_old(client):
    first = connect(client)
    connect(client)
    assert close_code(first) == Close.REPLACED


def reply_messages(ws) -> tuple[list[dict], bytes]:
    msgs, received = [], b""
    while not msgs or msgs[-1]["type"] not in ("reply.end", "error"):
        msg = ws.receive()
        if msg.get("bytes"):
            received += msg["bytes"][1:]
        elif msg.get("text"):
            msgs.append(json.loads(msg["text"]))
    return msgs, received


def test_failure_after_audio_ends_the_reply_with_error(client):
    client.app.state.reply_source = failing_source(TurnError("tts_failed"), chunks=3)
    ws = connect(client)
    turn = speak(ws, 0.5)
    msgs, received = reply_messages(ws)
    assert [m["type"] for m in msgs] == ["state", "state", "reply.start", "reply.end"]
    assert msgs[-1] == {"type": "reply.end", "turn": turn, "reason": "error"}
    assert len(received) == 3 * AUDIO_CHUNK  # queued chunks play before the error ends it


def test_abort_while_speaking_closes_the_source(client):
    closed = asyncio.Event()

    async def endless(pcm: bytes):
        try:
            while True:
                yield pcm[:AUDIO_CHUNK]
        finally:
            closed.set()

    client.app.state.reply_source = endless
    ws = connect(client)
    turn = speak(ws, 0.5)
    while ws.receive_json()["type"] != "reply.start":
        pass
    ws.send_json({"type": "abort", "turn": turn})
    while (msg := ws.receive()).get("text") is None:
        pass  # audio sent before the abort
    assert json.loads(msg["text"]) == {"type": "reply.end", "turn": turn, "reason": "abort"}
    assert closed.is_set()


def test_chunks_stream_in_order_and_the_cap_stops_the_source(client, monkeypatch):
    monkeypatch.setattr(session_module, "SPEECH_CAP", 2 * AUDIO_CHUNK)
    made = []

    async def chunks(pcm: bytes):
        for n in range(1, 10):
            made.append(n)
            yield bytes([n, 0]) * (AUDIO_CHUNK // 2)

    client.app.state.reply_source = chunks
    ws = connect(client)
    turn = speak(ws, 0.5)
    msgs, received = reply_messages(ws)
    assert msgs[-1] == {"type": "reply.end", "turn": turn, "reason": "cap"}
    assert received == bytes([1, 0]) * (AUDIO_CHUNK // 2) + bytes([2, 0]) * (AUDIO_CHUNK // 2)
    # played, queued, one waiting to be queued, one being made: the prefetch is bounded
    assert len(made) <= 2 + session_module.PREFETCH + 2

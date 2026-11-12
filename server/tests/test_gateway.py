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


def failing_source(error: Exception):
    async def source(pcm: bytes) -> bytes:
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


def voice_env(monkeypatch, **extra):
    for name, value in {**ENDPOINTS, "OTA_PIPELINE": "voice", **extra}.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("extra", "match"),
    [
        ({}, "no moderation"),  # roles are fine, the acknowledgement is missing
        ({"OTA_VOICE_WITHOUT_MODERATION": "1", "OTA_TTS_MODEL": ""}, "needs these roles: tts"),
    ],
)
def test_voice_pipeline_refuses_to_start(monkeypatch, extra, match):
    voice_env(monkeypatch, **extra)
    try:
        with pytest.raises(ConfigError, match=match), TestClient(create_app()):
            pass
    finally:
        get_settings.cache_clear()


def test_voice_pipeline_starts_with_acknowledgement(monkeypatch, caplog):
    voice_env(monkeypatch, OTA_VOICE_WITHOUT_MODERATION="1", OTA_MODERATION_MODEL="mod-m")
    try:
        app = create_app()
        with TestClient(app):
            assert isinstance(app.state.reply_source, VoicePipeline)
        assert "WITHOUT moderation" in caplog.text
        assert "moderation role is configured but not used" in caplog.text
    finally:
        get_settings.cache_clear()


def test_abort_while_thinking_ends_the_reply(client):
    async def slow(pcm: bytes) -> bytes:
        await asyncio.Event().wait()  # never answers
        return pcm

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
    msgs = []
    while True:
        msg = ws.receive()
        if msg.get("text"):
            msgs.append(json.loads(msg["text"]))
            if msgs[-1]["type"] == "reply.end":
                break
    assert msgs[-1] == {"type": "reply.end", "turn": turn, "reason": "cap"}


def test_new_connection_replaces_old(client):
    first = connect(client)
    connect(client)
    assert close_code(first) == Close.REPLACED

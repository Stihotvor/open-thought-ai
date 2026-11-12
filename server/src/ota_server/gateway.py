"""WebSocket endpoint: auth, hello, one session at a time (a new one replaces the old one)."""

import asyncio
import contextlib
import hashlib
import hmac
import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from .config import Settings, get_settings
from .protocol import HELLO_TIMEOUT, PROTO_VERSION, Close, ProtocolError, parse_text
from .ratelimit import TokenBucket
from .session import DeviceSession

log = logging.getLogger(__name__)
router = APIRouter()


class SessionSlot:
    """Holds the one live session. One server instance serves exactly one device."""

    def __init__(self) -> None:
        self.ws: WebSocket | None = None
        self.turns: TokenBucket | None = None  # kept across reconnects, so a reconnect is no reset

    def turn_limit(self, settings: Settings) -> TokenBucket:
        if self.turns is None:
            self.turns = TokenBucket(settings.turns_per_minute / 60, settings.turn_burst)
        return self.turns

    async def take(self, ws: WebSocket) -> None:
        old, self.ws = self.ws, ws
        if old is not None:
            log.warning("session replaced by a new connection")
            with contextlib.suppress(RuntimeError):  # old socket already closed
                await old.close(code=Close.REPLACED)

    def release(self, ws: WebSocket) -> None:
        if self.ws is ws:
            self.ws = None


slot = SessionSlot()


def token_ok(header: str | None, expected_sha256: str) -> bool:
    if not header or not header.startswith("Bearer "):
        return False
    digest = hashlib.sha256(header.removeprefix("Bearer ").encode()).hexdigest()
    return hmac.compare_digest(digest, expected_sha256)


async def read_hello(ws: WebSocket) -> dict:
    raw = await asyncio.wait_for(ws.receive_text(), timeout=HELLO_TIMEOUT)
    msg = parse_text(raw)
    if msg["type"] != "hello":
        raise ProtocolError("first message must be hello")
    return msg


@router.websocket("/v1/device")
async def device_endpoint(ws: WebSocket, settings: Settings = Depends(get_settings)) -> None:
    await ws.accept()
    if not token_ok(ws.headers.get("authorization"), settings.device_token_sha256):
        log.warning("auth failed from %s", ws.client.host if ws.client else "?")
        await ws.close(code=Close.BAD_TOKEN)
        return
    try:
        hello = await read_hello(ws)
    except (TimeoutError, ProtocolError):
        await ws.close(code=Close.PROTOCOL)
        return
    if hello.get("proto") != PROTO_VERSION:
        await ws.close(code=Close.BAD_VERSION)
        return

    await slot.take(ws)
    reply_source = ws.app.state.reply_source  # set at startup in main.py
    session = DeviceSession(ws, reply_source, turn_limit=slot.turn_limit(settings))
    await ws.send_json(
        {
            "type": "hello",
            "proto": PROTO_VERSION,
            "language": settings.language,
            "volume": settings.volume,
        }
    )
    try:
        while True:
            frame = await ws.receive()
            if frame["type"] == "websocket.disconnect":
                break
            if frame.get("text") is not None:
                await session.handle_text(parse_text(frame["text"]))
            elif frame.get("bytes") is not None:
                session.handle_audio(frame["bytes"])
    except ProtocolError as exc:
        log.warning("protocol error: %s", exc)
        await ws.close(code=Close.PROTOCOL)
    except WebSocketDisconnect:
        pass
    finally:
        slot.release(ws)  # first, so a failure below cannot keep the slot
        await session.cancel_reply()

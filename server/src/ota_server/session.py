"""One device session: turns, audio capture, paced playback with credit flow control."""

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import WebSocket

from .protocol import (
    AUDIO_CHUNK,
    BYTES_PER_SECOND,
    MAX_LEAD_SECONDS,
    MAX_TURN_AUDIO,
    MIN_SPEECH,
    START_CREDIT,
    ProtocolError,
    TurnError,
    split_audio,
)
from .ratelimit import TokenBucket

log = logging.getLogger(__name__)

# Turns captured audio into reply audio: echo (M2) or pipeline.VoicePipeline (M3).
# It raises TurnError(code) when the turn fails; the device then gets that error code.
ReplySource = Callable[[bytes], Awaitable[bytes]]


async def echo(pcm: bytes) -> bytes:
    return pcm


class DeviceSession:
    def __init__(
        self,
        ws: WebSocket,
        reply_source: ReplySource = echo,
        turn_limit: TokenBucket | None = None,
    ) -> None:
        self.turn_limit = turn_limit
        self.ws = ws
        self.reply_source = reply_source
        self.turn = 0
        self.listening = False
        self.audio = bytearray()
        self.credit = 0
        self.credit_changed = asyncio.Event()
        self.reply_task: asyncio.Task | None = None

    async def handle_text(self, msg: dict) -> None:
        match msg["type"]:
            case "listen.start":
                if self.turn_limit is not None and self.turn_limit.reserve() > 0:
                    self.turn_limit.cancel()  # refused: the token was not used
                    await self.ws.send_json(
                        {"type": "error", "turn": self.turn, "code": "too_many_turns"}
                    )
                    return
                await self.cancel_reply()
                self.turn = (self.turn + 1) % 256
                self.audio.clear()
                self.listening = True
                self.credit = START_CREDIT  # credit counts per turn and adds up from here
                await self.ws.send_json({"type": "turn.start", "turn": self.turn})
            case "listen.stop":
                if msg.get("turn") == self.turn and self.listening:
                    self.listening = False
                    self.reply_task = asyncio.create_task(self.reply(self.turn, bytes(self.audio)))
            case "abort":
                if msg.get("turn") == self.turn:
                    await self.cancel_reply()
            case "audio.credit":
                granted = msg.get("bytes")
                if not isinstance(granted, int) or granted <= 0:
                    raise ProtocolError("bad credit")
                self.credit += granted
                self.credit_changed.set()
            case other:
                log.info("ignored message type %s", other)

    def handle_audio(self, frame: bytes) -> None:
        turn, pcm = split_audio(frame)
        if turn != self.turn or not self.listening:
            return  # stale frame from an older turn
        if len(self.audio) + len(pcm) > MAX_TURN_AUDIO:
            self.listening = False  # 30 s limit: stop collecting, keep what we have
            return
        self.audio.extend(pcm)

    async def reply(self, turn: int, pcm: bytes) -> None:
        if len(pcm) < MIN_SPEECH:
            await self.ws.send_json({"type": "error", "turn": turn, "code": "no_speech"})
            return
        await self.ws.send_json({"type": "state", "turn": turn, "state": "thinking"})
        try:
            out = await self.reply_source(pcm)
        except TurnError as exc:
            await self.ws.send_json({"type": "error", "turn": turn, "code": exc.code})
            return
        except Exception:  # pylint: disable=broad-exception-caught  # the device must hear an error
            log.exception("reply failed")
            await self.ws.send_json({"type": "error", "turn": turn, "code": "internal"})
            return
        await self.ws.send_json({"type": "reply.start", "turn": turn})
        try:
            await self.stream(turn, out)
        except asyncio.CancelledError:
            await self.ws.send_json({"type": "reply.end", "turn": turn, "reason": "abort"})
            raise
        await self.ws.send_json({"type": "reply.end", "turn": turn, "reason": "done"})

    async def stream(self, turn: int, pcm: bytes) -> None:
        """Send PCM at real time, at most MAX_LEAD_SECONDS ahead, within the granted credit."""
        started = time.monotonic()
        for offset in range(0, len(pcm), AUDIO_CHUNK):
            chunk = pcm[offset : offset + AUDIO_CHUNK]
            while self.credit < len(chunk):
                self.credit_changed.clear()
                await self.credit_changed.wait()
            lead = offset / BYTES_PER_SECOND - (time.monotonic() - started)
            if lead > MAX_LEAD_SECONDS:
                await asyncio.sleep(lead - MAX_LEAD_SECONDS)
            self.credit -= len(chunk)
            await self.ws.send_bytes(bytes([turn]) + chunk)

    async def cancel_reply(self) -> None:
        task, self.reply_task = self.reply_task, None
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

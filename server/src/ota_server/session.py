"""One device session: turns, audio capture, paced playback with credit flow control."""

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncGenerator, Callable

from fastapi import WebSocket

from .protocol import (
    AUDIO_CHUNK,
    BYTES_PER_SECOND,
    MAX_LEAD_SECONDS,
    MAX_TURN_AUDIO,
    MIN_SPEECH,
    SPEECH_CAP,
    START_CREDIT,
    ProtocolError,
    TurnError,
    split_audio,
)
from .ratelimit import TokenBucket

log = logging.getLogger(__name__)

# Turns captured audio into reply audio chunks: echo (M2) or pipeline.VoicePipeline (M3).
# It raises TurnError(code) when the turn fails: before the first chunk the device gets
# that error code, after it the reply ends with reason "error".
ReplySource = Callable[[bytes], AsyncGenerator[bytes]]
PREFETCH = 2  # reply chunks made ahead of playback: TTS runs while the device plays
END = None  # the queue item after the last chunk


async def echo(pcm: bytes) -> AsyncGenerator[bytes]:
    yield pcm


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
        # The turn whose reply the device still waits for: set when the reply starts,
        # cleared by its last message. An abort then owes the device a reply.end.
        self.open_turn: int | None = None

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
                    self.start_reply()
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
            self.start_reply()  # 30 s limit: the server stops listening and answers
            return
        self.audio.extend(pcm)

    def start_reply(self) -> None:
        self.listening = False
        self.open_turn = self.turn
        self.reply_task = asyncio.create_task(self.reply(self.turn, bytes(self.audio)))

    async def reply(self, turn: int, pcm: bytes) -> None:
        if len(pcm) < MIN_SPEECH:
            await self.finish({"type": "error", "turn": turn, "code": "no_speech"})
            return
        await self.ws.send_json({"type": "state", "turn": turn, "state": "thinking"})
        queue: asyncio.Queue = asyncio.Queue(maxsize=PREFETCH)
        producer = asyncio.create_task(self.produce(pcm, queue))
        try:
            await self.play(turn, queue)
        finally:
            producer.cancel()  # cap, abort or error: stop the remaining TTS calls
            with contextlib.suppress(asyncio.CancelledError):
                await producer

    async def produce(self, pcm: bytes, queue: asyncio.Queue) -> None:
        """Put each reply chunk on the queue, then END, or the exception that ended it."""
        try:
            async with contextlib.aclosing(self.reply_source(pcm)) as chunks:
                async for chunk in chunks:
                    await queue.put(chunk)
            await queue.put(END)
        except Exception as exc:  # pylint: disable=broad-exception-caught  # play() reports it
            await queue.put(exc)

    async def play(self, turn: int, queue: asyncio.Queue) -> None:
        clock = time.monotonic()
        sent, reason, speaking = 0, "done", False  # speaking: reply.start was sent
        while (item := await queue.get()) is not END:
            if isinstance(item, Exception):
                code = item.code if isinstance(item, TurnError) else "internal"
                if not isinstance(item, TurnError):
                    log.error("reply failed", exc_info=item)
                if not speaking:
                    await self.finish({"type": "error", "turn": turn, "code": code})
                    return
                log.warning("reply failed after audio started: %s", code)
                reason = "error"
                break
            if not speaking:
                speaking = await self.start_speaking(turn)
                clock = time.monotonic()
            room = SPEECH_CAP - sent
            clock = await self.stream(turn, item[:room], sent, clock)
            sent += min(len(item), room)
            if len(item) > room:
                reason = "cap"
                break
        if not speaking:
            await self.start_speaking(turn)  # an empty reply still starts and ends
        await self.finish({"type": "reply.end", "turn": turn, "reason": reason})

    async def start_speaking(self, turn: int) -> bool:
        await self.ws.send_json({"type": "state", "turn": turn, "state": "speaking"})
        await self.ws.send_json({"type": "reply.start", "turn": turn})
        return True

    async def finish(self, msg: dict) -> None:
        """Send the last message of a turn. After it, an abort owes the device nothing.
        The turn is closed before the send, so an abort during the send adds no second end."""
        self.open_turn = None
        await self.ws.send_json(msg)

    async def send_quietly(self, msg: dict) -> None:
        """Send and ignore any error: used on abort, when the socket may be closed already."""
        with contextlib.suppress(Exception):
            await self.ws.send_json(msg)

    async def stream(self, turn: int, pcm: bytes, sent: int, clock: float) -> float:
        """Send PCM at real time, at most MAX_LEAD_SECONDS ahead, within the granted credit.
        `sent` bytes of this reply already went out since `clock` (monotonic time).
        Returns the clock, moved later when the audio came late (slow TTS), so the
        late audio is paced again and not sent as one burst."""
        for start in range(0, len(pcm), AUDIO_CHUNK):
            chunk = pcm[start : start + AUDIO_CHUNK]
            while self.credit < len(chunk):
                self.credit_changed.clear()
                await self.credit_changed.wait()
            position = (sent + start) / BYTES_PER_SECOND
            lead = position - (time.monotonic() - clock)
            if lead < 0:  # the device ran out of audio: pace from now
                clock, lead = time.monotonic() - position, 0.0
            if lead > MAX_LEAD_SECONDS:
                await asyncio.sleep(lead - MAX_LEAD_SECONDS)
            self.credit -= len(chunk)
            await self.ws.send_bytes(bytes([turn]) + chunk)
        return clock

    async def cancel_reply(self) -> None:
        task, self.reply_task = self.reply_task, None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # pylint: disable=broad-exception-caught  # e.g. a closed socket
                log.exception("reply task failed")
        if self.open_turn is not None:  # abort while thinking or speaking (docs/protocol.md)
            await self.send_quietly(
                {"type": "reply.end", "turn": self.open_turn, "reason": "abort"}
            )
            self.open_turn = None

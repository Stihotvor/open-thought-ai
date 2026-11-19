"""One device session: turns, audio capture, paced playback with credit flow control."""

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import AsyncGenerator
from dataclasses import replace
from typing import Any

from fastapi import WebSocket

from .hub.device_source import DeviceSource
from .hub.hub import McpHub
from .hub.pending import PendingCalls
from .hub.types import ToolContext
from .protocol import (
    AUDIO_CHUNK,
    BYTES_PER_SECOND,
    CREDIT_TIMEOUT,
    MAX_CREDIT,
    MAX_LEAD_SECONDS,
    MAX_TURN_AUDIO,
    MIN_SPEECH,
    SPEECH_CAP,
    START_CREDIT,
    TRANSCRIPT_MAX_CHARS,
    ProtocolError,
    TurnError,
    split_audio,
)
from .ratelimit import TokenBucket
from .reply import History, Notify, ReplyChunk, ReplySource, no_notify

log = logging.getLogger(__name__)


PREFETCH = 2  # reply chunks made ahead of playback: TTS runs while the device plays
END = None  # the queue item after the last chunk
# Chunks, then END, or the exception that ended the source.
ReplyQueue = asyncio.Queue[ReplyChunk | Exception | None]


class CreditStalled(Exception):
    """The device gave no audio credit for CREDIT_TIMEOUT seconds."""


async def echo(
    pcm: bytes,
    _history: History,
    *,
    notify: Notify = no_notify,
    tools: ToolContext | None = None,
) -> AsyncGenerator[ReplyChunk]:
    yield ReplyChunk(pcm)


class DeviceSession:  # pylint: disable=too-many-instance-attributes  # one device's state
    def __init__(
        self,
        ws: WebSocket,
        reply_source: ReplySource = echo,
        turn_limit: TokenBucket | None = None,
        history_turns: int = 15,
    ) -> None:
        # Chat memory of this connection only: what the device really played.
        self.history: deque[tuple[str, str]] = deque(maxlen=history_turns)
        self.turn_limit = turn_limit
        self.ws = ws
        self.reply_source = reply_source
        self.turn = 0
        self.listening = False
        self.audio = bytearray()
        self.credit = 0
        self.credit_changed = asyncio.Event()
        self.reply_task: asyncio.Task[None] | None = None
        # The turn whose reply the device still waits for: set when the reply starts,
        # cleared by its last message. An abort then owes the device a reply.end.
        self.open_turn: int | None = None
        self.pending = PendingCalls()  # tool calls that wait for the device's tool.result
        self.hub = McpHub([DeviceSource(self.ws.send_json, self.pending)])  # one per session

    async def handle_text(self, msg: dict[str, Any]) -> None:
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
                self.credit = min(self.credit + granted, MAX_CREDIT)
                self.credit_changed.set()
            case "tool.result":  # in every state: the reply task waits for it
                self.pending.resolve(msg)
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
        history = tuple(self.history)  # a snapshot: the source never sees later changes
        self.reply_task = asyncio.create_task(self.reply(self.turn, bytes(self.audio), history))

    async def reply(self, turn: int, pcm: bytes, history: History = ()) -> None:
        # A cheap length pre-filter: MIN_SPEECH_MS in vad.py is the real speech check.
        if len(pcm) < MIN_SPEECH:
            await self.finish({"type": "error", "turn": turn, "code": "no_speech"})
            return
        await self.ws.send_json({"type": "state", "turn": turn, "state": "thinking"})
        queue: ReplyQueue = asyncio.Queue(maxsize=PREFETCH)
        producer = asyncio.create_task(self.produce(pcm, history, queue, self.notifier(turn)))
        played: list[ReplyChunk] = []
        try:
            await self.play(turn, queue, played)
        finally:
            self.remember(played)  # also after the cap, an error or an abort
            producer.cancel()  # cap, abort or error: stop the remaining TTS calls
            with contextlib.suppress(asyncio.CancelledError):
                await producer

    def remember(self, played: list[ReplyChunk]) -> None:
        """Keep the question and the chunks the device played. A chunk cut by the cap, an
        abort or an error ends with "…", so "continue" goes on from there."""
        texts = [chunk.text for chunk in played if chunk.text]
        if texts:
            self.history.append((played[0].question, " ".join(texts)))

    def notifier(self, turn: int) -> Notify:
        """The source's Notify for this turn: it sends the transcript while the turn is open."""

        async def notify(text: str) -> None:
            # Not after an abort or the turn's last message. It does not stop a call after the
            # first chunk: ReplySource requires `notify` before the first chunk.
            if self.open_turn == turn:
                await self.ws.send_json(
                    {"type": "transcript", "turn": turn, "text": text[:TRANSCRIPT_MAX_CHARS]}
                )

        return notify

    async def produce(
        self, pcm: bytes, history: History, queue: ReplyQueue, notify: Notify
    ) -> None:
        """Put each reply chunk on the queue, then END, or the exception that ended it."""
        try:
            source = self.reply_source(pcm, history, notify=notify, tools=self.turn_tools())
            async with contextlib.aclosing(source) as chunks:
                async for chunk in chunks:
                    await queue.put(chunk)
            await queue.put(END)
        except Exception as exc:  # pylint: disable=broad-exception-caught  # play() reports it
            await queue.put(exc)

    def turn_tools(self) -> ToolContext:
        """The session's hub with a new CallCtx: the call cap lives for one turn (D5, D7)."""
        return self.hub.turn_context()

    async def play(self, turn: int, queue: ReplyQueue, played: list[ReplyChunk]) -> None:
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
            played.append(replace(item, text=f"{item.text} …" if item.text else ""))  # cut
            try:
                clock = await self.stream(turn, item.pcm[:room], sent, clock)
            except CreditStalled:
                log.warning("device gave no audio credit for %s s: reply ends", CREDIT_TIMEOUT)
                reason = "error"
                break
            sent += min(len(item.pcm), room)
            if len(item.pcm) > room:
                reason = "cap"
                break
            played[-1] = item  # played in full
        if not speaking:
            await self.start_speaking(turn)  # an empty reply still starts and ends
        await self.finish({"type": "reply.end", "turn": turn, "reason": reason})

    async def start_speaking(self, turn: int) -> bool:
        await self.ws.send_json({"type": "state", "turn": turn, "state": "speaking"})
        await self.ws.send_json({"type": "reply.start", "turn": turn})
        return True

    async def finish(self, msg: dict[str, Any]) -> None:
        """Send the last message of a turn. After it, an abort owes the device nothing.
        The turn is closed before the send, so an abort during the send adds no second end.
        If that abort cancels the send itself, the device gets no end message: it already
        flushed its audio on the button press (docs/protocol.md), so nothing is lost."""
        self.open_turn = None
        await self.ws.send_json(msg)

    async def send_quietly(self, msg: dict[str, Any]) -> None:
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
                try:
                    await asyncio.wait_for(self.credit_changed.wait(), CREDIT_TIMEOUT)
                except TimeoutError:
                    raise CreditStalled() from None
            position = (sent + start) / BYTES_PER_SECOND
            lead = position - (time.monotonic() - clock)
            if lead < 0:  # the device ran out of audio: pace from now
                clock, lead = time.monotonic() - position, 0.0
            if lead > MAX_LEAD_SECONDS:
                await asyncio.sleep(lead - MAX_LEAD_SECONDS)
            self.credit -= len(chunk)
            await self.ws.send_bytes(bytes([turn]) + chunk)
        return clock

    async def close(self) -> None:
        """The socket closed: end every waiting tool call, then the reply."""
        self.pending.fail_all()
        await self.cancel_reply()

    async def cancel_reply(self) -> None:
        task, self.reply_task = self.reply_task, None
        if task and not task.done():
            task.cancel()
            await asyncio.wait([task])  # a cancel aimed at the caller still reaches it
        if task and task.done() and not task.cancelled() and task.exception():
            log.error("reply task failed", exc_info=task.exception())  # e.g. a closed socket
        if self.open_turn is not None:  # abort while thinking or speaking (docs/protocol.md)
            await self.send_quietly(
                {"type": "reply.end", "turn": self.open_turn, "reason": "abort"}
            )
            self.open_turn = None

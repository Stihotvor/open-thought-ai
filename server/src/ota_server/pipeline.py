"""One voice turn: STT -> chat -> TTS -> device PCM (docs/architecture.md, `pipeline`).

Moderation (docs/security.md), fail closed with the `moderated` code:
- Self-test: before the first voice turn, a known-harmful and a harmless phrase in the
  device language must get the right verdicts. Until they do, every turn is refused.
- The transcript (in pieces, plus the whole) is checked before chat.
- Every TTS chunk, plus the question with the reply so far, is checked before TTS audio
  is played.
- A flagged text, or any moderation failure (error, timeout, rate limit, unknown result
  shape), refuses the turn.

Streaming, per sentence: a chat task cuts complete sentences (SENTENCE_END) out of the
chat stream while the turn goes on. Sentence 1 is a chunk alone, later sentences are
merged up to TTS_CHUNK. Each chunk is moderated and synthesized at the same time, and its
audio is yielded only once moderation passed. So the first sound waits for sentence 1,
not for the whole reply. A flag on a later chunk stops the turn after the chunks that
already played. The session closes the generator at the 60 s cap, which cancels the
chat task and the remaining TTS calls.

History: the session keeps what the device played and passes it in. The newest turns that
fit in MAX_HISTORY_CHARS go to chat. "continue" works through it.

VAD and transcript: `trim_speech` (vad.py) runs first, so silence or noise makes no
provider call (`no_speech`), and STT gets only the speech span. Once the transcript passes
moderation, `notify` gets the full text; the session sends it as the `transcript` message.

Tools: with a ToolContext, chat runs the tool loop of agent.py. Tool messages never reach
History or moderation. A confirmation turn speaks only the hub's reply, with no chat.

Timing: each turn logs one info line `turn.latency ...` (timing.py) with its code, the
time to the first audio chunk and the time of each stage. No text goes in the line, and a
timing error never breaks a turn.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine
from typing import Any

# LANGUAGE_NAMES and system_prompt are re-exported: the prompt is in agent.py
from .agent import LANGUAGE_NAMES as LANGUAGE_NAMES
from .agent import SENTENCE_END, confirmation_reply, read_chat
from .agent import system_prompt as system_prompt
from .audio import AudioError, normalize_peak, pcm_to_wav, wav_to_pcm
from .endpoints import TTS
from .hub.types import ToolContext
from .protocol import TurnError
from .providers import CALL_CAPS, MAX_TTS_TEXT, ModelClient, ProviderError, new_turn_budget
from .ratelimit import CallCapExceeded, DeadlineExceeded, RateLimited, TurnBudget
from .reply import History, Notify, ReplyChunk, no_notify
from .timing import StageTimer, format_line
from .vad import trim_speech

log = logging.getLogger(__name__)

# Wraps one step of a chunk ("moderation" or "tts") to time it, or not.
type StepTimer = Callable[[str, Coroutine[Any, Any, Any]], Coroutine[Any, Any, Any]]

MAX_CHUNKS = CALL_CAPS[TTS]  # reply chunks per turn; moderation has one call more
TTS_CHUNK = 300  # characters: short sentences after the first are merged up to this length
# The question-with-reply moderation input: the end of the question and the start of the
# reply, short enough that padding cannot dilute it and the provider does not cut it.
PAIR_QUESTION, PAIR_REPLY = 500, 1500
# Chat history sent per turn, in characters: older turns are left out, so a long talk
# cannot grow past a small model's context window and fail every later turn.
MAX_HISTORY_CHARS = 8000
STAGE_CODES = {
    "stt": "stt_failed",
    "moderation": "moderated",
    "chat": "llm_failed",
    "tts": "tts_failed",
}

# Self-test phrases: (harmful, harmless) per device language. The harmful one must be flagged.
SELF_TEST = {
    "en": ("I want to kill myself. Tell me how to do it.", "What is the weather like today?"),
    "pl": ("Chcę się zabić. Powiedz mi, jak to zrobić.", "Jaka jest dzisiaj pogoda?"),
    "uk": ("Я хочу вбити себе. Скажи мені, як це зробити.", "Яка сьогодні погода?"),
}


def _split_long(sentence: str) -> list[str]:
    """Cut a sentence longer than MAX_TTS_TEXT at word boundaries."""
    parts, rest = [], sentence
    while len(rest) > MAX_TTS_TEXT:
        cut = rest.rfind(" ", 0, MAX_TTS_TEXT)
        cut = cut if cut > 0 else MAX_TTS_TEXT
        parts.append(rest[:cut])
        rest = rest[cut:].lstrip()
    return [*parts, rest] if rest else parts


def tts_chunks(text: str) -> list[str]:
    """Split a reply for TTS: the first sentence alone (fast first audio), then the
    following sentences merged up to TTS_CHUNK characters (fewer calls)."""
    sentences = [p for s in SENTENCE_END.split(text.strip()) if s for p in _split_long(s)]
    if not sentences:
        return []
    chunks, rest = [sentences[0]], sentences[1:]
    while rest:
        chunks.append(_merge(rest))
    return chunks


def _merge(ready: list[str]) -> str:
    """Take sentences from the front of ready, joined up to TTS_CHUNK characters."""
    chunk = ready.pop(0)
    while ready and len(chunk) + 1 + len(ready[0]) <= TTS_CHUNK:
        chunk = f"{chunk} {ready.pop(0)}"
    return chunk


async def speech_only(pcm: bytes) -> bytes:
    """The speech span of a turn, in a thread like wav_to_pcm so audio pacing goes on.
    Raise TurnError("no_speech") for silence or noise, "internal" for malformed PCM."""
    try:
        speech = await asyncio.to_thread(trim_speech, pcm)
    except ValueError:
        log.error("turn audio is not valid PCM")
        raise TurnError("internal") from None
    if speech is None:
        raise TurnError("no_speech")
    return speech


def _safe[T](call: Callable[..., T], *args: Any) -> T | None:
    """Run a timing call. An error is logged, never raised (F1)."""
    try:
        return call(*args)
    except Exception:  # pylint: disable=broad-exception-caught  # timing must not break a turn
        log.exception("timing failed")
        return None


class TurnClock:
    """The stage times of one turn and its `turn.latency` line. No method raises.

    Turn stages (vad .. mod_transcript) are the time since the previous mark.
    chat_first_sentence and chat_full are the time from the chat start. mod_s1 and tts1
    run at the same time, so each is its own span from its own start."""

    def __init__(self, turn_id: int) -> None:
        self.turn_id = turn_id
        self.code = "ok"
        self.latency_ms: int | None = None
        self._turn = _safe(StageTimer)
        self._chat = _safe(StageTimer)
        self._spans: dict[str, int] = {}
        self._sentence_seen = False
        if self._turn is not None:
            _safe(self._turn.start)

    def mark(self, stage: str) -> None:
        if self._turn is not None:
            _safe(self._turn.mark, stage)

    def chat_started(self) -> None:
        if self._chat is not None:
            _safe(self._chat.start)

    def first_sentence(self) -> None:
        """Mark chat_first_sentence once, at the first complete sentence."""
        if self._sentence_seen or self._chat is None:
            return
        self._sentence_seen = True
        _safe(self._chat.mark, "chat_first_sentence")

    def chat_done(self) -> None:
        """chat_full: the time from the chat start to the end of the stream."""
        if self._chat is not None:
            full = _safe(self._chat.latency_ms)
            if full is not None:
                self._spans["chat_full"] = full

    async def timed[T](self, stage: str | None, step: Awaitable[T]) -> T:
        """Await step. With a stage name, store how long it took (only if it succeeds)."""
        if stage is None:
            return await step
        span = _safe(StageTimer)
        if span is not None:
            _safe(span.start)
        result = await step
        if span is not None:
            _safe(span.mark, stage)
            self._spans.update(span.stages)
        return result

    def fail(self, code: str) -> None:
        """A failed turn logs its code and no latency."""
        self.code, self.latency_ms = code, None

    def closed(self) -> None:
        """The session closed or cancelled the turn: the latency stays."""
        self.code = "closed"

    def first_audio(self) -> None:
        if self.latency_ms is None and self._turn is not None:
            self.latency_ms = _safe(self._turn.latency_ms)

    def _line(self) -> str:
        stages = {**(self._chat.stages if self._chat else {}), **self._spans}
        stages.update(self._turn.stages if self._turn else {})
        return format_line(self.turn_id, self.code, self.latency_ms, stages)

    def log(self) -> None:
        line = _safe(self._line)
        if line is not None:
            log.info("%s", line)


class SentenceFeed:
    """Reply sentences from the chat task to the turn, as TTS chunks: sentence 1 alone
    (fast first audio), then all sentences that are ready, merged up to TTS_CHUNK."""

    def __init__(self) -> None:
        # One item per put (a sentence), then one end item: None, or the chat error.
        self._queue: asyncio.Queue[str | Exception | None] = asyncio.Queue()
        self._error: Exception | None = None
        self._ready: list[str] = []
        self._ended = False
        self._first_to_go = True  # sentence 1 still goes out alone

    def put(self, sentence: str) -> None:
        for part in _split_long(sentence.strip()):
            self._queue.put_nowait(part)

    def end(self, error: Exception | None = None) -> None:
        """The chat stream is over. With error, next() raises it once the sentences
        before it are taken."""
        self._queue.put_nowait(error)

    async def next(self) -> str | None:
        """The next chunk, or None when the chat stream has ended and all are taken.
        A chat error is raised after the sentences that came before it."""
        if not self._ready and not self._ended:
            self._take(await self._queue.get())
        while not self._queue.empty():
            self._take(self._queue.get_nowait())
        if not self._ready:
            if self._error is not None:
                raise self._error
            return None
        if self._first_to_go:
            self._first_to_go = False
            return self._ready.pop(0)
        return _merge(self._ready)

    def _take(self, item: str | Exception | None) -> None:
        if isinstance(item, Exception):
            self._ended, self._error = True, item
        elif item is None:
            self._ended = True
        else:
            self._ready.append(item)


async def staged[T](stage: str, step: Awaitable[T]) -> T:
    """Await one provider step. Map its failure to the TurnError code of the stage."""
    try:
        return await step
    except RateLimited:
        # Moderation fails closed: its rate limit refuses the turn like any other failure.
        raise TurnError("moderated" if stage == "moderation" else "rate_limited") from None
    except (ProviderError, DeadlineExceeded, CallCapExceeded, AudioError) as exc:
        log.warning("turn failed at %s: %s", stage, type(exc).__name__)
        raise TurnError(STAGE_CODES[stage]) from None


def _stage_timer(clock: TurnClock, index: int) -> StepTimer:
    """The timing wrapper for chunk index: chunk 0 logs mod_s1 and tts1, others nothing."""
    names = {"moderation": "mod_s1", "tts": "tts1"} if index == 0 else {}
    return lambda step, awaitable: clock.timed(names.get(step), awaitable)


def _cancel_quietly(task: asyncio.Task[Any]) -> None:
    """Cancel a task nobody will await. Its result or error no longer matters, so it is
    taken when the task ends: asyncio does not log it. The task is not awaited, so a
    provider call or a TTS thread may finish its cleanup after the turn."""
    task.add_done_callback(_retrieve)
    task.cancel()


def _retrieve(task: asyncio.Task[Any]) -> None:
    if not task.cancelled():
        task.exception()


class VoicePipeline:  # pylint: disable=too-few-public-methods  # a callable ReplySource
    """A ReplySource for DeviceSession: device PCM in, reply PCM out."""

    def __init__(self, client: ModelClient, language: str, voice: str) -> None:
        self.client = client
        self.language = language
        self.voice = voice
        self.moderation_checked = False
        self.turns = 0  # the id of the latest turn in the turn.latency line

    async def check_moderation(self) -> None:
        """Run the self-test once. Raise TurnError("moderated") until it passes."""
        if self.moderation_checked:
            return
        harmful, harmless = SELF_TEST[self.language]
        try:
            verdicts = await self.client.moderate([harmful, harmless], new_turn_budget())
        except (ProviderError, RateLimited, DeadlineExceeded, CallCapExceeded):
            log.error("moderation self-test could not run: voice turns are refused")
            raise TurnError("moderated") from None
        if verdicts != [True, False]:
            log.error(
                "moderation self-test failed for language %s (verdicts %s): voice turns "
                "are refused",
                self.language,
                verdicts,
            )
            raise TurnError("moderated")
        log.info("moderation self-test passed for language %s", self.language)
        self.moderation_checked = True

    async def moderate(self, side: str, texts: list[str], budget: TurnBudget) -> None:
        """Raise TurnError("moderated") when any text is flagged. Logs the side, not the text.

        The harmful self-test phrase rides along as a canary in every batch. If it is not
        flagged, moderation stopped working since the self-test (another model, a stub, a
        fallback route): the turn is refused and the self-test runs again next turn."""
        if not texts:
            raise TurnError("moderated")  # an empty batch proves nothing
        canary = SELF_TEST[self.language][0]
        *verdicts, canary_flagged = await self.client.moderate([*texts, canary], budget)
        if not canary_flagged:
            self.moderation_checked = False
            log.error("moderation canary was not flagged: voice turns are refused")
            raise TurnError("moderated")
        if any(verdicts):
            log.warning("moderation flagged the %s", side)
            raise TurnError("moderated")

    def messages(self, text: str, history: History) -> list[dict[str, Any]]:
        """The chat input: system prompt, the newest remembered turns that fit in
        MAX_HISTORY_CHARS, then the new question."""
        past: list[dict[str, Any]] = []
        size = 0
        for question, reply in reversed(history):
            size += len(question) + len(reply)
            if size > MAX_HISTORY_CHARS:
                break
            past[:0] = [
                {"role": "user", "content": question},
                {"role": "assistant", "content": reply},
            ]
        return [
            {"role": "system", "content": system_prompt(self.language)},
            *past,
            {"role": "user", "content": text},
        ]

    async def synthesize(self, chunk: str, budget: TurnBudget) -> bytes:
        """The PCM of one chunk."""
        wav = await self.client.speak(chunk, self.voice, budget)
        # A thread, so the event loop keeps running (it gets GIL time slices). The thread
        # cannot be cancelled, so check the deadline once it is done.
        pcm = await asyncio.to_thread(lambda: normalize_peak(wav_to_pcm(wav)))
        if budget.left() <= 0:
            raise DeadlineExceeded()
        return pcm

    async def checked_audio(
        self,
        chunk: str,
        pair: str,
        budget: TurnBudget,
        timed: StepTimer,
    ) -> bytes:
        """Moderate a chunk (with the question-and-reply pair) and synthesize it at the same
        time. The audio is returned only once moderation passed: a flag cancels the TTS.
        timed(step_name, step) wraps each step, so the caller decides what is timed."""
        tts: asyncio.Task[bytes] = asyncio.create_task(timed("tts", self.synthesize(chunk, budget)))
        try:
            await staged(
                "moderation", timed("moderation", self.moderate("reply", [chunk, pair], budget))
            )
        except BaseException:
            _cancel_quietly(tts)
            raise
        return await staged("tts", tts)

    async def read_chat(
        self,
        messages: list[dict[str, Any]],
        budget: TurnBudget,
        feed: SentenceFeed,
        clock: TurnClock,
        tools: ToolContext | None = None,
    ) -> None:
        """The chat task: sentences go on feed while the turn moderates and speaks."""
        await read_chat(self.client, messages, budget, feed, clock, tools)

    async def speak(
        self,
        question: str,
        feed: SentenceFeed,
        budget: TurnBudget,
        clock: TurnClock,
    ) -> AsyncGenerator[ReplyChunk]:
        """Yield each chunk as audio once it passed moderation, with its text for the
        session history. A chat error stops the reply after the sentences before it.
        The reply ends at MAX_CHUNKS chunks, within the TTS and moderation call caps."""
        reply = ""
        for index in range(MAX_CHUNKS):
            chunk = await staged("chat", feed.next())
            if chunk is None:
                break
            reply = f"{reply} {chunk}".lstrip()
            # "Yes, take ten" is only harmful as the answer to a question: each chunk goes
            # with the end of the question and the end of the reply so far (this chunk).
            pair = f"{question[-PAIR_QUESTION:]}\n{reply[-PAIR_REPLY:]}"
            pcm = await self.checked_audio(chunk, pair, budget, _stage_timer(clock, index))
            paused = budget.clock()
            yield ReplyChunk(pcm, chunk, question)
            # The session waits here while the device plays: that time is not TTS work.
            budget.extend(budget.clock() - paused)
        else:
            log.info("reply cut at %d chunks", MAX_CHUNKS)
        if not reply:
            raise TurnError("llm_failed")

    async def __call__(
        self,
        pcm: bytes,
        history: History,
        *,
        notify: Notify = no_notify,
        tools: ToolContext | None = None,
    ) -> AsyncGenerator[ReplyChunk]:
        """One turn, and its turn.latency line however it ends."""
        self.turns += 1
        clock = TurnClock(self.turns)
        turn = self._turn(pcm, history, notify, clock, tools)
        try:
            async with contextlib.aclosing(turn) as chunks:
                async for chunk in chunks:
                    clock.first_audio()
                    yield chunk
        except TurnError as err:
            clock.fail(err.code)
            raise
        except Exception:
            clock.fail("internal")
            raise
        except BaseException:  # the session closed or cancelled the turn: latency stays
            clock.closed()
            raise
        finally:
            clock.log()

    async def _turn(
        self,
        pcm: bytes,
        history: History,
        notify: Notify,
        clock: TurnClock,
        tools: ToolContext | None,
    ) -> AsyncGenerator[ReplyChunk]:
        speech = await speech_only(pcm)  # before any provider call, the self-test too
        clock.mark("vad")
        selftest = not self.moderation_checked
        await self.check_moderation()
        if selftest:
            clock.mark("selftest")
        budget = new_turn_budget()
        text = await staged(
            "stt", self.client.transcribe(pcm_to_wav(speech), self.language, budget)
        )
        text = text.strip()
        clock.mark("stt")
        if not text:
            raise TurnError("no_speech")
        pieces = tts_chunks(text)  # each piece within the provider's input size
        await staged(
            "moderation",
            self.moderate("transcript", [*pieces, text] if len(pieces) > 1 else pieces, budget),
        )
        clock.mark("mod_transcript")
        clock.chat_started()
        await notify(text)  # the full text: the session cuts it for the device
        feed = SentenceFeed()
        chat: asyncio.Task[None] | None = None
        # The chat task never waits for playback (the feed has no size limit), so the
        # turn deadline bounds only its own work.
        if not await confirmation_reply(tools, text, feed):  # else no chat at all (D8)
            chat = asyncio.create_task(
                self.read_chat(self.messages(text, history), budget, feed, clock, tools)
            )
        try:
            async with contextlib.aclosing(self.speak(text, feed, budget, clock)) as audio:
                async for chunk in audio:
                    yield chunk
        finally:
            if chat is not None:
                _cancel_quietly(chat)

"""One voice turn: STT -> chat -> TTS -> device PCM (docs/architecture.md, `pipeline`).

Moderation (docs/security.md), fail closed with the `moderated` code:
- Self-test: before the first voice turn, a known-harmful and a harmless phrase in the
  device language must get the right verdicts. Until they do, every turn is refused.
- The transcript (in pieces, plus the whole) is checked before chat.
- Every TTS chunk, plus the question with the whole reply, is checked before TTS.
- A flagged text, or any moderation failure (error, timeout, rate limit, unknown result
  shape), refuses the turn.

Streaming: the whole reply is moderated before any sound (the question with the whole
reply is one input), then each TTS chunk is yielded as soon as it is spoken, so the device
plays the first sentence while the next one is synthesized. The session closes the
generator at the 60 s cap, which stops the remaining TTS calls.

Not yet here (next steps in M3): the `transcript` message, history, VAD and "continue".
"""

import asyncio
import contextlib
import logging
import re
from collections.abc import AsyncGenerator

from .audio import AudioError, pcm_to_wav, wav_to_pcm
from .protocol import TurnError
from .providers import MAX_TTS_TEXT, ModelClient, ProviderError, new_turn_budget
from .ratelimit import CallCapExceeded, DeadlineExceeded, RateLimited, TurnBudget

log = logging.getLogger(__name__)

LANGUAGE_NAMES = {"en": "English", "pl": "Polish", "uk": "Ukrainian"}
TTS_CHUNK = 300  # characters: short sentences after the first are merged up to this length
# The question-with-reply moderation input: the end of the question and the start of the
# reply, short enough that padding cannot dilute it and the provider does not cut it.
PAIR_QUESTION, PAIR_REPLY = 500, 1500
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
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


def system_prompt(language: str) -> str:
    name = LANGUAGE_NAMES[language]
    return (
        f"You are a friendly voice assistant for a family with children. Always answer in {name}. "
        "Keep answers short: one to three sentences. Use plain spoken words: no lists, no "
        "markdown, no emoji. Keep every answer safe for children. Never ask for personal data "
        "such as a name, an address or a school. Never tell the child to keep a secret from "
        "the parents. Never give medicine doses or medical instructions: say to ask a parent "
        "or a doctor."
    )


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
    chunks = [sentences[0]]
    current = ""
    for sentence in sentences[1:]:
        if current and len(current) + 1 + len(sentence) > TTS_CHUNK:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}" if current else sentence
    return [*chunks, current] if current else chunks


class VoicePipeline:  # pylint: disable=too-few-public-methods  # a callable ReplySource
    """A ReplySource for DeviceSession: device PCM in, reply PCM out."""

    def __init__(self, client: ModelClient, language: str, voice: str) -> None:
        self.client = client
        self.language = language
        self.voice = voice
        self.moderation_checked = False

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

    async def __call__(self, pcm: bytes) -> AsyncGenerator[bytes]:
        await self.check_moderation()
        budget = new_turn_budget()
        stage = "stt"
        try:
            text = (await self.client.transcribe(pcm_to_wav(pcm), self.language, budget)).strip()
            if not text:
                raise TurnError("no_speech")
            stage = "moderation"
            pieces = tts_chunks(text)  # each piece within the provider's input size
            await self.moderate(
                "transcript", [*pieces, text] if len(pieces) > 1 else pieces, budget
            )
            stage = "chat"
            messages = [
                {"role": "system", "content": system_prompt(self.language)},
                {"role": "user", "content": text},
            ]
            async with contextlib.aclosing(self.client.chat(messages, budget)) as deltas:
                reply = "".join([delta async for delta in deltas]).strip()
            if not reply:
                raise TurnError("llm_failed")
            chunks = tts_chunks(reply)
            stage = "moderation"
            # Each chunk, plus the question with the whole reply: "Yes, take ten" is only
            # harmful as the answer to a question.
            pair = f"{text[-PAIR_QUESTION:]}\n{reply[:PAIR_REPLY]}"
            await self.moderate("reply", [*chunks, pair], budget)
            stage = "tts"
            for chunk in chunks:
                wav = await self.client.speak(chunk, self.voice, budget)
                # A thread, so the event loop keeps running (it gets GIL time slices). The thread
                # cannot be cancelled, so check the deadline once it is done.
                pcm = await asyncio.to_thread(wav_to_pcm, wav)
                if budget.left() <= 0:
                    raise DeadlineExceeded()
                paused = budget.clock()
                yield pcm
                # The session waits here while the device plays: that time is not TTS work.
                budget.extend(budget.clock() - paused)
        except RateLimited:
            # Moderation fails closed: its rate limit refuses the turn like any other failure.
            raise TurnError("moderated" if stage == "moderation" else "rate_limited") from None
        except (ProviderError, DeadlineExceeded, CallCapExceeded, AudioError) as exc:
            log.warning("turn failed at %s: %s", stage, type(exc).__name__)
            raise TurnError(STAGE_CODES[stage]) from None

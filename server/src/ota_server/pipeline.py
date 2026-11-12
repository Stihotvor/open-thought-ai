"""One voice turn: STT -> chat -> TTS -> device PCM (docs/architecture.md, `pipeline`).

This first version builds the whole reply, then the session streams it.
Not yet here (next steps in M3): per-sentence streaming to the device, moderation,
history, VAD and "continue". Until moderation exists, the voice pipeline is opt-in
(`OTA_PIPELINE=voice`) and not for use with children.
"""

import contextlib
import logging
import re

from .audio import AudioError, pcm_to_wav, wav_to_pcm
from .protocol import TurnError
from .providers import MAX_TTS_TEXT, ModelClient, ProviderError, new_turn_budget
from .ratelimit import CallCapExceeded, DeadlineExceeded, RateLimited

log = logging.getLogger(__name__)

LANGUAGE_NAMES = {"en": "English", "pl": "Polish", "uk": "Ukrainian"}
TTS_CHUNK = 300  # characters: short sentences after the first are merged up to this length
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
STAGE_CODES = {"stt": "stt_failed", "chat": "llm_failed", "tts": "tts_failed"}


def system_prompt(language: str) -> str:
    name = LANGUAGE_NAMES[language]
    return (
        f"You are a friendly voice assistant for a family with children. Always answer in {name}. "
        "Keep answers short: one to three sentences. Use plain spoken words: no lists, no "
        "markdown, no emoji. Keep every answer safe for children."
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

    async def __call__(self, pcm: bytes) -> bytes:
        budget = new_turn_budget()
        stage = "stt"
        try:
            text = (await self.client.transcribe(pcm_to_wav(pcm), self.language, budget)).strip()
            if not text:
                raise TurnError("no_speech")
            stage = "chat"
            messages = [
                {"role": "system", "content": system_prompt(self.language)},
                {"role": "user", "content": text},
            ]
            async with contextlib.aclosing(self.client.chat(messages, budget)) as deltas:
                reply = "".join([delta async for delta in deltas]).strip()
            if not reply:
                raise TurnError("llm_failed")
            stage = "tts"
            out = bytearray()
            for chunk in tts_chunks(reply):
                out += wav_to_pcm(await self.client.speak(chunk, self.voice, budget))
            return bytes(out)
        except RateLimited:
            raise TurnError("rate_limited") from None
        except (ProviderError, DeadlineExceeded, CallCapExceeded, AudioError) as exc:
            log.warning("turn failed at %s: %s", stage, type(exc).__name__)
            raise TurnError(STAGE_CODES[stage]) from None

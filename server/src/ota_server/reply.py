"""The contract between a device session and its reply source (echo or the voice pipeline)."""

from collections.abc import AsyncGenerator, Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class ReplyChunk:
    """Reply audio, with the text it speaks and the question it answers (for history)."""

    pcm: bytes
    text: str = ""
    question: str = ""


# (question, played reply) pairs of earlier turns, oldest first.
History = Sequence[tuple[str, str]]
# Turns captured audio (and the history) into reply chunks: echo (M2) or
# pipeline.VoicePipeline (M3). It raises TurnError(code) when the turn fails: before the
# first chunk the device gets that error code, after it the reply ends with reason "error".
ReplySource = Callable[[bytes, History], AsyncGenerator[ReplyChunk]]

"""The contract between a device session and its reply source (echo or the voice pipeline)."""

from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ReplyChunk:
    """Reply audio, with the text it speaks and the question it answers (for history)."""

    pcm: bytes
    text: str = ""
    question: str = ""


# (question, played reply) pairs of earlier turns, oldest first.
History = Sequence[tuple[str, str]]
# Tells the device what it heard: the session sends it as the `transcript` message.
Notify = Callable[[str], Awaitable[None]]


async def no_notify(_text: str) -> None:
    """The default Notify: tells nobody."""


class ReplySource(Protocol):  # pylint: disable=too-few-public-methods  # a callable type
    """Turns captured audio (and the history) into reply chunks: echo (M2) or
    pipeline.VoicePipeline (M3). It raises TurnError(code) when the turn fails: before the
    first chunk the device gets that error code, after it the reply ends with reason "error".
    It may call `notify` with the transcript before the first chunk."""

    def __call__(
        self, pcm: bytes, history: History, /, *, notify: Notify = no_notify
    ) -> AsyncGenerator[ReplyChunk]: ...

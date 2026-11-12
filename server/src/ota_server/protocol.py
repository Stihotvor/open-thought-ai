"""Protocol v1 constants and frame checks. See docs/protocol.md."""

import json
from enum import IntEnum

PROTO_VERSION = 1


SAMPLE_RATE = 16_000
BYTES_PER_SECOND = SAMPLE_RATE * 2  # 16-bit mono PCM

MAX_TEXT_FRAME = 1024
MAX_AUDIO_FRAME = 3201  # 1 byte turn id + 100 ms of PCM
AUDIO_CHUNK = 3200  # PCM bytes per frame the server sends (100 ms)
MAX_TURN_AUDIO = 30 * BYTES_PER_SECOND
MIN_SPEECH = int(0.3 * BYTES_PER_SECOND)

START_CREDIT = 9600
MAX_LEAD_SECONDS = 0.3
HELLO_TIMEOUT = 5.0
SPEECH_CAP = 60 * BYTES_PER_SECOND  # fixed 60 s reply cap: longer replies end with reason "cap"


class TurnError(Exception):
    """A turn failed. `code` is the protocol error code the device gets (docs/protocol.md)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Close(IntEnum):
    REPLACED = 4000
    PROTOCOL = 4400
    BAD_TOKEN = 4401
    BAD_VERSION = 4426


class ProtocolError(Exception):
    """The peer broke protocol v1. The connection closes with code 4400."""


def parse_text(raw: str) -> dict:
    if len(raw.encode()) > MAX_TEXT_FRAME:
        raise ProtocolError("text frame too large")
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError("bad json") from exc
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
        raise ProtocolError("message without type")
    return msg


def split_audio(frame: bytes) -> tuple[int, bytes]:
    if not 1 < len(frame) <= MAX_AUDIO_FRAME:
        raise ProtocolError("bad audio frame size")
    return frame[0], frame[1:]

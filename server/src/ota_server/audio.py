"""Audio format helpers: device PCM to WAV for STT, and TTS WAV back to device PCM.

Device audio is PCM 16 kHz, 16-bit, mono, little-endian (docs/protocol.md).
Python 3.13 removed `audioop`, so the resampler is a small pure-Python linear one.
It has no low-pass filter: content above 8 kHz in a 22-24 kHz voice aliases a little.
That is fine for speech on a small speaker.
"""

import io
import sys
import wave
from array import array

from .protocol import SAMPLE_RATE


class AudioError(Exception):
    """The audio is not a WAV file the server can play."""


def pcm_to_wav(pcm: bytes) -> bytes:
    """Wrap device PCM in a WAV header, for STT endpoints that expect a file."""
    out = io.BytesIO()
    with wave.Wave_write(out) as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return out.getvalue()


def _samples(raw: bytes) -> array:
    samples = array("h")
    samples.frombytes(raw[: len(raw) - len(raw) % 2])
    if sys.byteorder == "big":
        samples.byteswap()  # WAV is little-endian
    return samples


def _to_bytes(samples: array) -> bytes:
    if sys.byteorder == "big":
        samples = array("h", samples)
        samples.byteswap()
    return samples.tobytes()


def _downmix(samples: array, channels: int) -> array:
    if channels == 1:
        return samples
    frames = len(samples) // channels
    return array(
        "h",
        (sum(samples[i * channels : (i + 1) * channels]) // channels for i in range(frames)),
    )


def resample(samples: array, src_rate: int, dst_rate: int) -> array:
    """Linear interpolation from src_rate to dst_rate."""
    if src_rate == dst_rate or not samples:
        return samples
    count = len(samples) * dst_rate // src_rate
    step = src_rate / dst_rate
    last = len(samples) - 1
    out = array("h", bytes(2 * count))
    for i in range(count):
        pos = i * step
        j = int(pos)
        a = samples[j]
        b = samples[j + 1 if j < last else last]
        out[i] = int(a + (b - a) * (pos - j))
    return out


def wav_to_pcm(data: bytes) -> bytes:
    """Decode a 16-bit WAV (any rate, mono or stereo) to device PCM at 16 kHz mono."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            if w.getsampwidth() != 2:
                raise AudioError("only 16-bit WAV is supported")
            channels, rate = w.getnchannels(), w.getframerate()
            raw = w.readframes(w.getnframes())
    except (wave.Error, EOFError) as exc:
        raise AudioError("not a WAV file") from exc
    if channels not in (1, 2) or rate <= 0:
        raise AudioError("unsupported channel count or rate")
    return _to_bytes(resample(_downmix(_samples(raw), channels), rate, SAMPLE_RATE))

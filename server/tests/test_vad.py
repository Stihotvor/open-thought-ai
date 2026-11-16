import random
import time
from array import array

import pytest

from ota_server.protocol import BYTES_PER_SECOND, SAMPLE_RATE
from ota_server.vad import trim_speech


def silence(ms: int) -> bytes:
    return bytes(SAMPLE_RATE * ms // 1000 * 2)


def tone(ms: int, amplitude: int = 8000) -> bytes:
    """Square wave at 400 Hz (period of 40 samples)."""
    n = SAMPLE_RATE * ms // 1000
    return array("h", [amplitude if i % 40 < 20 else -amplitude for i in range(n)]).tobytes()


def noise(ms: int, amplitude: int = 300) -> bytes:
    rng = random.Random(1)
    n = SAMPLE_RATE * ms // 1000
    return array("h", [rng.randint(-amplitude, amplitude) for _ in range(n)]).tobytes()


def test_speech_between_silences_is_trimmed():
    result = trim_speech(silence(1000) + tone(1000) + silence(1000))
    assert result is not None
    assert len(result) <= 3 * BYTES_PER_SECOND // 2


def test_silence_is_rejected():
    assert trim_speech(silence(3000)) is None


def test_low_noise_is_rejected():
    assert trim_speech(noise(3000)) is None


def test_short_speech_is_rejected():
    assert trim_speech(silence(1000) + tone(250) + silence(1000)) is None


def test_enough_speech_is_kept():
    assert trim_speech(silence(1000) + tone(400) + silence(1000)) is not None


def test_speed():
    pcm = (noise(1000) + tone(1000)) * 15
    best = min(_timed(pcm) for _ in range(3))
    assert best <= 0.050


def _timed(pcm: bytes) -> float:
    start = time.perf_counter()
    trim_speech(pcm)
    return time.perf_counter() - start


def test_bad_audio():
    with pytest.raises(ValueError):
        trim_speech(bytes(641))

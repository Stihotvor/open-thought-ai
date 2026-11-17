import time

import pytest
from fakes import noise, silence, tone

from ota_server.protocol import BYTES_PER_SECOND
from ota_server.vad import trim_speech


def test_speech_between_silences_is_trimmed() -> None:
    result = trim_speech(silence(1000) + tone(1000) + silence(1000))
    assert result is not None
    assert len(result) <= 3 * BYTES_PER_SECOND // 2


def test_silence_is_rejected() -> None:
    assert trim_speech(silence(3000)) is None


def test_low_noise_is_rejected() -> None:
    assert trim_speech(noise(3000)) is None


def test_short_speech_is_rejected() -> None:
    assert trim_speech(silence(1000) + tone(250) + silence(1000)) is None


def test_enough_speech_is_kept() -> None:
    assert trim_speech(silence(1000) + tone(400) + silence(1000)) is not None


def test_speed() -> None:
    pcm = (noise(1000) + tone(1000)) * 15
    best = min(_timed(pcm) for _ in range(3))
    assert best <= 0.050


def _timed(pcm: bytes) -> float:
    start = time.perf_counter()
    trim_speech(pcm)
    return time.perf_counter() - start


def test_bad_audio() -> None:
    with pytest.raises(ValueError):
        trim_speech(bytes(641))

import io
import wave
from array import array

import pytest

from ota_server.audio import (
    MAX_GAIN,
    TARGET_PEAK,
    AudioError,
    normalize_peak,
    pcm_to_wav,
    resample,
    wav_to_pcm,
)
from ota_server.protocol import BYTES_PER_SECOND


def make_wav(samples: list[int], rate: int, channels: int = 1, width: int = 2) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        if width == 2:
            w.writeframes(array("h", samples).tobytes())
        else:
            w.writeframes(bytes(len(samples)))
    return out.getvalue()


def test_device_pcm_round_trip() -> None:
    pcm = array("h", [0, 1000, -1000, 32767, -32768]).tobytes()
    assert wav_to_pcm(pcm_to_wav(pcm)) == pcm


def test_tts_rate_is_resampled_to_16k() -> None:
    half_second_24k = make_wav([500] * 12000, rate=24000)
    assert len(wav_to_pcm(half_second_24k)) == BYTES_PER_SECOND // 2


def test_linear_interpolation() -> None:
    assert list(resample(array("h", [0, 100, 200, 300]), 3, 2)) == [0, 150]


def test_stereo_downmix_truncates_toward_zero() -> None:
    stereo = make_wav([-1, -2], rate=16000, channels=2)
    assert list(array("h", wav_to_pcm(stereo))) == [-1]


def test_stereo_is_downmixed() -> None:
    stereo = make_wav([1000, 3000, 1000, 3000], rate=16000, channels=2)
    assert list(array("h", wav_to_pcm(stereo))) == [2000, 2000]


@pytest.mark.parametrize(
    "data",
    [
        b"not a wav",
        make_wav([0, 0], rate=16000, width=1),
        make_wav([0] * 100, rate=100),  # a fake rate would blow up the resampler
        make_wav([0, 0], rate=96000),
        make_wav([0] * (8000 * 61), rate=8000),  # one chunk longer than 60 s
    ],
)
def test_unplayable_audio_is_refused(data: bytes) -> None:
    with pytest.raises(AudioError):
        wav_to_pcm(data)


def pcm(samples: list[int]) -> bytes:
    return array("h", samples).tobytes()


def test_normalize_raises_quiet_speech_to_the_target_peak() -> None:
    # Mistral TTS peaks near -9 dBFS: about 11400 of 32767.
    out = array("h", normalize_peak(pcm([11400, -5700, 0])))
    assert max(abs(s) for s in out) == TARGET_PEAK
    assert out[1] == -round(5700 * TARGET_PEAK / 11400)
    assert out[2] == 0


def test_normalize_gain_is_capped() -> None:
    # Near silence must not turn into loud noise.
    out = array("h", normalize_peak(pcm([100, -50])))
    assert list(out) == [100 * MAX_GAIN, -50 * MAX_GAIN]


def test_normalize_never_lowers_loud_audio() -> None:
    loud = pcm([32767, -32768, 1000])
    assert normalize_peak(loud) == loud


def test_normalize_keeps_silence_and_empty_audio() -> None:
    assert normalize_peak(pcm([0, 0])) == pcm([0, 0])
    assert normalize_peak(b"") == b""

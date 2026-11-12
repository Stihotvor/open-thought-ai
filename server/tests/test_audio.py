import io
import wave
from array import array

import pytest

from ota_server.audio import AudioError, pcm_to_wav, resample, wav_to_pcm
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


def test_device_pcm_round_trip():
    pcm = array("h", [0, 1000, -1000, 32767, -32768]).tobytes()
    assert wav_to_pcm(pcm_to_wav(pcm)) == pcm


def test_tts_rate_is_resampled_to_16k():
    half_second_24k = make_wav([500] * 12000, rate=24000)
    assert len(wav_to_pcm(half_second_24k)) == BYTES_PER_SECOND // 2


def test_linear_interpolation():
    assert list(resample(array("h", [0, 100, 200, 300]), 3, 2)) == [0, 150]


def test_stereo_downmix_truncates_toward_zero():
    stereo = make_wav([-1, -2], rate=16000, channels=2)
    assert list(array("h", wav_to_pcm(stereo))) == [-1]


def test_stereo_is_downmixed():
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
def test_unplayable_audio_is_refused(data):
    with pytest.raises(AudioError):
        wav_to_pcm(data)

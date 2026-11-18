"""T4: make_phrase_audio with a fake speaches (httpx.MockTransport). No network."""

import io
import json
import wave
from pathlib import Path

import httpx
import pytest
from make_phrase_audio import PHRASES, main


def wav_bytes(rate: int = 22_050, seconds: float = 0.1) -> bytes:
    out = io.BytesIO()
    with wave.Wave_write(out) as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(rate * seconds))
    return out.getvalue()


class FakeSpeaches:
    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy
        self.speech_calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            if not self.healthy:
                raise httpx.ConnectError("connection refused", request=request)
            return httpx.Response(200, json={"status": "ok"})
        assert request.url.path == "/v1/audio/speech"
        self.speech_calls += 1
        return httpx.Response(200, content=wav_bytes())

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def one_phrase_file(tmp_path: Path) -> Path:
    path = tmp_path / "phrases.json"
    path.write_text('{"phrases":[{"id":"p01","en":"Hi","pl":"Cześć","uk":"Привіт"}]}', "utf-8")
    return path


def test_speaches_down_stops_before_synthesis(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeSpeaches(healthy=False)
    code = main(["--out", str(tmp_path / "out")], client=fake.client())
    assert code != 0
    assert "speaches" in capsys.readouterr().err
    assert fake.speech_calls == 0
    assert not (tmp_path / "out").exists()


def test_wav_is_16k_mono_s16(tmp_path: Path) -> None:
    fake = FakeSpeaches()
    args = ["--out", str(tmp_path), "--phrases", str(one_phrase_file(tmp_path))]
    assert main(args, client=fake.client()) == 0
    with wave.open(str(tmp_path / "en" / "p01.wav"), "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16_000, 1, 2)
        assert w.getnframes() > 0


def test_one_file_per_phrase_and_language(tmp_path: Path) -> None:
    fake = FakeSpeaches()
    assert main(["--out", str(tmp_path), "--phrases", str(PHRASES)], client=fake.client()) == 0
    assert len(list(tmp_path.glob("*/*.wav"))) == 90
    for lang in ("en", "pl", "uk"):
        assert len(list((tmp_path / lang).glob("*.wav"))) == 30
    assert fake.speech_calls == 90


@pytest.mark.parametrize("bad_id", ["../p01", "p 01", "", "p01/x"])
def test_bad_phrase_id_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], bad_id: str
) -> None:
    path = tmp_path / "phrases.json"
    path.write_text(json.dumps({"phrases": [{"id": bad_id, "en": "a", "pl": "b", "uk": "c"}]}))
    fake = FakeSpeaches()
    code = main(["--out", str(tmp_path / "out"), "--phrases", str(path)], client=fake.client())
    assert code == 1
    assert "phrase id" in capsys.readouterr().err
    assert fake.speech_calls == 0
    assert not (tmp_path / "out").exists()


def test_voice_options_are_used(tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200)
        seen.append(json.loads(request.content)["voice"])
        return httpx.Response(200, content=wav_bytes())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    args = ["--out", str(tmp_path), "--phrases", str(one_phrase_file(tmp_path)), "--voice-pl", "x"]
    assert main(args, client=client) == 0
    assert seen == ["en_US-amy-medium", "x", "uk_UA-ukrainian_tts-medium"]

"""Make the latency-run input audio: one 16 kHz mono s16le WAV per phrase and language (T4).

Reads docs/research/m3-phrases.json, asks speaches (Piper voices) for each phrase and writes
<out>/<lang>/<id>.wav. The audio is not committed; the default out dir is git-ignored.

Usage (from server/): python scripts/make_phrase_audio.py [--base-url URL] [--out DIR]
    [--voice-en V] [--voice-pl V] [--voice-uk V] [--phrases FILE]
"""

import argparse
import json
import re
import sys
from pathlib import Path

import httpx

from ota_server.audio import AudioError, pcm_to_wav, wav_to_pcm

REPO = Path(__file__).resolve().parents[2]
PHRASES = REPO / "docs" / "research" / "m3-phrases.json"
OUT = REPO / "latency-audio"
BASE_URL = "http://localhost:8000/v1"
VOICES = {  # Piper voices served by speaches as speaches-ai/piper-<voice>; verify in the guide
    "en": "en_US-amy-medium",
    "pl": "pl_PL-gosia-medium",
    "uk": "uk_UA-ukrainian_tts-medium",
}
TIMEOUT = 120.0  # seconds; the first call may load the model
PHRASE_ID = re.compile(r"[A-Za-z0-9_-]+")  # the id is a file name: no path parts


class ScriptError(Exception):
    """A clear message for the user; main prints it and exits non-zero."""


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=BASE_URL, help="speaches OpenAI-style base URL")
    parser.add_argument("--out", type=Path, default=OUT, help="output dir (<out>/<lang>/<id>.wav)")
    parser.add_argument("--phrases", type=Path, default=PHRASES, help="phrase JSON file")
    for lang, voice in VOICES.items():
        parser.add_argument(f"--voice-{lang}", default=voice, help=f"Piper voice for {lang}")
    args = parser.parse_args(argv)
    args.voices = {lang: vars(args)[f"voice_{lang}"] for lang in VOICES}
    return args


def health_url(base_url: str) -> str:
    root = base_url.rstrip("/").removesuffix("/v1")
    return f"{root}/health"


def check_health(client: httpx.Client, base_url: str) -> None:
    url = health_url(base_url)
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        raise ScriptError(f"speaches is not reachable at {url} ({exc}); start it first") from None
    if response.status_code != 200:
        raise ScriptError(f"speaches health check at {url} returned {response.status_code}")


def load_phrases(path: Path) -> list[dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    phrases: list[dict[str, str]] = data["phrases"]
    for phrase in phrases:
        if not PHRASE_ID.fullmatch(str(phrase["id"])):
            raise ScriptError(f"phrase id {phrase['id']!r} must match {PHRASE_ID.pattern}")
    return phrases


def synthesize(client: httpx.Client, base_url: str, text: str, voice: str) -> bytes:
    body = {
        "model": f"speaches-ai/piper-{voice}",
        "voice": voice,
        "input": text,
        "response_format": "wav",
    }
    url = f"{base_url.rstrip('/')}/audio/speech"
    try:
        response = client.post(url, json=body)
        response.raise_for_status()
        return pcm_to_wav(wav_to_pcm(response.content))
    except (httpx.HTTPError, AudioError) as exc:
        raise ScriptError(f"speaches speech call failed for voice {voice}: {exc}") from None


def write_all(client: httpx.Client, args: argparse.Namespace) -> int:
    count = 0
    voices: dict[str, str] = args.voices
    for phrase in load_phrases(args.phrases):
        for lang, voice in voices.items():
            target = args.out / lang / f"{phrase['id']}.wav"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(synthesize(client, args.base_url, phrase[lang], voice))
            count += 1
    return count


def main(argv: list[str] | None = None, client: httpx.Client | None = None) -> int:
    args = parse_args(argv)
    http = client or httpx.Client(timeout=TIMEOUT)
    try:
        check_health(http, args.base_url)
        count = write_all(http, args)
    except ScriptError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if client is None:
            http.close()
    print(f"wrote {count} WAV files to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

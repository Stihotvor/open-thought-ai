"""Offline checks of the latency-run guide and of the no-audio rule (issue #26)."""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GUIDE_PATH = REPO / "docs" / "guides" / "latency-run.md"


@pytest.fixture
def guide() -> str:
    return GUIDE_PATH.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "value",
    [
        "make_phrase_audio.py",
        "ota_server.latency_run",
        "ota_server.latency_report",
        "LiteLLM checklist (issue #20)",
        "p50 <= 5 s",
    ],
)
def test_guide_names_the_tools_and_target(guide: str, value: str) -> None:
    assert value in guide


def test_no_audio_in_the_phrase_folder() -> None:
    assert not list((REPO / "docs").rglob("*.wav"))
    assert "latency-audio/" in (REPO / ".gitignore").read_text(encoding="utf-8")

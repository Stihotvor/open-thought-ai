"""Offline checks of the LiteLLM guide and of compose.yaml (ADR 0002: LiteLLM stays external)."""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GUIDE_PATH = REPO / "docs" / "guides" / "litellm-fallback.md"
COMPOSE_PATH = REPO / "compose.yaml"
MAX_SETUP_STEPS = 6


@pytest.fixture
def guide() -> str:
    return GUIDE_PATH.read_text(encoding="utf-8")


def test_compose_has_no_litellm() -> None:
    assert "berriai/litellm" not in COMPOSE_PATH.read_text(encoding="utf-8")


def test_compose_passes_env_file_to_the_server() -> None:
    # Without it, OTA_PIPELINE and the model settings in .env never reach the container.
    assert "    env_file: .env\n" in COMPOSE_PATH.read_text(encoding="utf-8")


def test_guide_setup_steps(guide: str) -> None:
    steps = guide.split("## Setup steps", 1)[1].split("\n## ", 1)[0]
    numbers = [line.split(".", 1)[0] for line in steps.splitlines() if line[:1].isdigit()]
    assert numbers == [str(n) for n in range(1, MAX_SETUP_STEPS + 1)]


@pytest.mark.parametrize(
    "value",
    [
        "lm_studio/<model-id>",
        "LM_STUDIO_API_BASE",
        "OTA_ALLOW_INSECURE_MODEL_ENDPOINT=1",
        "OTA_CHAT_MODEL=mistral-small-latest",
        "OTA_TTS_MODEL=tts",
        "#26",
    ],
)
def test_guide_names_contract_values(guide: str, value: str) -> None:
    assert value in guide

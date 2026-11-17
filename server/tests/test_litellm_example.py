"""Offline checks of the example LiteLLM config in docs/examples/litellm (T3)."""

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO / "docs" / "examples" / "litellm" / "config.yaml"
GUIDE_PATH = REPO / "docs" / "guides" / "litellm-fallback.md"
CHAT = "mistral-small-latest"
ENV_PREFIX = "os.environ/"
TURN_DEADLINE_S = 8

Config = dict[str, Any]


def load_config() -> Config:
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def model_names(config: Config) -> set[str]:
    return {str(entry["model_name"]) for entry in config["model_list"]}


def fallbacks(config: Config) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for entry in config["router_settings"]["fallbacks"]:
        for key, targets in entry.items():
            result[str(key)] = [str(t) for t in targets]
    return result


def undefined_targets(config: Config) -> list[str]:
    names = model_names(config)
    return [
        name
        for key, targets in fallbacks(config).items()
        for name in [key, *targets]
        if name not in names
    ]


def literal_secrets(config: Config) -> list[str]:
    bad: list[str] = []
    for entry in config["model_list"]:
        params: dict[str, Any] = entry["litellm_params"]
        for field in ("api_key", "api_base"):
            value = params.get(field)
            if value is not None and not str(value).startswith(ENV_PREFIX):
                bad.append(f"{entry['model_name']}.{field}")
    return bad


def without_gemini(config: Config) -> Config:
    variant = copy.deepcopy(config)
    variant["model_list"] = [m for m in variant["model_list"] if m["model_name"] != "chat-gemini"]
    for entry in variant["router_settings"]["fallbacks"]:
        for key, targets in entry.items():
            entry[key] = [t for t in targets if t != "chat-gemini"]
    return variant


@pytest.fixture
def config() -> Config:
    return load_config()


def test_fallback_targets_are_defined(config: Config) -> None:
    assert undefined_targets(config) == []


def test_wrong_target_fails_check(config: Config) -> None:
    config["router_settings"]["fallbacks"].append({CHAT: ["chat-missing"]})
    assert undefined_targets(config) == ["chat-missing"]


def test_secrets_come_from_env(config: Config) -> None:
    assert literal_secrets(config) == []


def test_literal_key_fails_check(config: Config) -> None:
    config["model_list"][0]["litellm_params"]["api_key"] = "sk-literal"
    assert literal_secrets(config) == [f"{CHAT}.api_key"]


def test_chain_order(config: Config) -> None:
    chains = fallbacks(config)
    assert [CHAT, *chains[CHAT]] == [CHAT, "chat-gemini", "chat-local"]
    assert chains["voxtral-mini-latest"] == ["stt-local"]
    assert chains["tts"] == ["tts-local"]


def chat_time_s(config: Config) -> float:
    """The worst-case chat time: one timeout for each chat group in the chain."""
    chat_groups = 1 + len(fallbacks(config)[CHAT])
    return float(config["router_settings"]["timeout"]) * chat_groups


def test_time_budget(config: Config) -> None:
    assert int(config["router_settings"]["num_retries"]) <= 1
    assert chat_time_s(config) <= TURN_DEADLINE_S


def test_no_gemini_variant(config: Config) -> None:
    variant = without_gemini(config)
    assert fallbacks(variant)[CHAT] == ["chat-local"]
    assert undefined_targets(variant) == []


def params(config: Config, name: str) -> dict[str, Any]:
    entry = next(m for m in config["model_list"] if m["model_name"] == name)
    result: dict[str, Any] = entry["litellm_params"]
    return result


@pytest.fixture
def guide() -> str:
    return GUIDE_PATH.read_text(encoding="utf-8")


def test_local_audio_groups_use_speaches(config: Config) -> None:
    for name in ("stt-local", "tts-local"):
        assert params(config, name)["api_base"] == f"{ENV_PREFIX}SPEACHES_API_BASE"


def test_literal_gemini_key_is_named(config: Config) -> None:
    params(config, "chat-gemini")["api_key"] = "sk-test-1234"
    assert literal_secrets(config) == ["chat-gemini.api_key"]


def test_typo_in_chain_is_named(config: Config) -> None:
    config["router_settings"]["fallbacks"][0][CHAT] = ["chat-gemin", "chat-local"]
    assert undefined_targets(config) == ["chat-gemin"]


def test_long_timeout_breaks_the_deadline(config: Config) -> None:
    config["router_settings"]["timeout"] = 4
    assert chat_time_s(config) == 12
    assert chat_time_s(config) > TURN_DEADLINE_S


def test_no_moderation_group(config: Config) -> None:
    assert not [name for name in model_names(config) if "moderation" in name]


def test_compose_has_no_litellm() -> None:
    assert "image: ghcr.io/berriai/litellm" not in (REPO / "compose.yaml").read_text(
        encoding="utf-8"
    )


def test_guide_has_six_setup_steps(guide: str) -> None:
    steps = guide.split("## Setup steps", 1)[1].split("\n## ", 1)[0]
    numbers = [line.split(".", 1)[0] for line in steps.splitlines() if line[:1].isdigit()]
    assert numbers == ["1", "2", "3", "4", "5", "6"]


@pytest.mark.parametrize(
    "text",
    [
        "lm_studio/<model-id>",  # E2
        "LM_STUDIO_API_BASE",
        "Set only the `OTA_CHAT_*` lines",  # E3
        "#26",  # F4
        "8 s",  # F3
        "OTA_ALLOW_INSECURE_MODEL_ENDPOINT=1",  # LAN rule
        "OTA_CHAT_MODEL=mistral-small-latest",  # T7
        "virtual key",
    ],
)
def test_guide_mentions(guide: str, text: str) -> None:
    assert text in guide

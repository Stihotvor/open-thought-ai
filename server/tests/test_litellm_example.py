"""Offline checks of the example LiteLLM config in docs/examples/litellm (T3)."""

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

CONFIG_PATH = Path(__file__).resolve().parents[2] / "docs" / "examples" / "litellm" / "config.yaml"
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


def params(config: Config, name: str) -> dict[str, Any]:
    entry = next(m for m in config["model_list"] if m["model_name"] == name)
    result: dict[str, Any] = entry["litellm_params"]
    return result


@pytest.fixture
def config() -> Config:
    return load_config()


def test_fallback_targets_are_defined(config: Config) -> None:
    assert undefined_targets(config) == []


@pytest.mark.parametrize("wrong", ["chat-missing", "chat-gemin"])
def test_wrong_target_is_named(config: Config, wrong: str) -> None:
    config["router_settings"]["fallbacks"][0][CHAT] = [wrong, "chat-local"]
    assert undefined_targets(config) == [wrong]


def test_secrets_come_from_env(config: Config) -> None:
    assert literal_secrets(config) == []


@pytest.mark.parametrize("group", [CHAT, "chat-gemini"])
def test_literal_key_is_named(config: Config, group: str) -> None:
    params(config, group)["api_key"] = "sk-literal"
    assert literal_secrets(config) == [f"{group}.api_key"]


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
    assert int(config["router_settings"]["num_retries"]) == 0  # a retry doubles the chat time
    assert chat_time_s(config) <= TURN_DEADLINE_S


def test_no_gemini_variant(config: Config) -> None:
    variant = without_gemini(config)
    assert fallbacks(variant)[CHAT] == ["chat-local"]
    assert undefined_targets(variant) == []


def test_long_timeout_breaks_the_deadline(config: Config) -> None:
    config["router_settings"]["timeout"] = TURN_DEADLINE_S / 2
    assert chat_time_s(config) > TURN_DEADLINE_S


def test_local_audio_groups_use_speaches(config: Config) -> None:
    for name in ("stt-local", "tts-local"):
        assert params(config, name)["api_base"] == f"{ENV_PREFIX}SPEACHES_API_BASE"


def test_no_moderation_group(config: Config) -> None:
    assert not [name for name in model_names(config) if "moderation" in name]

"""Offline checks of the MCP servers guide: the JSON example, setting names and structure."""

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from ota_server.config import Settings
from ota_server.mcp.config import PIN, SECRET_VAR, load_servers

REPO = Path(__file__).resolve().parents[2]
GUIDE_PATH = REPO / "docs" / "guides" / "mcp-servers.md"
README_PATH = REPO / "README.md"
MAX_SETUP_STEPS = 6
MAX_LINES = 120
JSON_BLOCK = re.compile(r"```json\n(.*?)```", re.DOTALL)
STEP = re.compile(r"^(\d+)\.", re.MULTILINE)
SECRET_TOKEN = re.compile(r"MCP_SECRET_[A-Z0-9_]+")
OTA_TOKEN = re.compile(r"OTA_[A-Z0-9_]+")
ALLOWED_HOSTS_LINE = re.compile(r"^OTA_MCP_ALLOWED_HOSTS=(\S+)$", re.MULTILINE)
REQUIRED_SETTINGS = ("mcp_servers_file", "mcp_allowed_hosts")
DENYLIST = ("n8n-nodes-langchain", "mcpTrigger", "toolWorkflow", "/webhook/", "/mcp-test/")
REQUIRED_TEXT = (
    "Troubleshooting",
    "OTA_MCP_ALLOWED_HOSTS",
    "no_confirm",
    "see your n8n UI",
    "current hash sha256:",
)
SYMPTOMS = ("Tool missing", "Tool vanished", "LAN or private", "skipped", "n8n down", "secret")


@pytest.fixture
def guide() -> str:
    return GUIDE_PATH.read_text(encoding="utf-8")


@pytest.fixture
def example_block(guide: str) -> str:
    return _first_json_block(guide)


@pytest.fixture
def example(example_block: str) -> dict[str, Any]:
    entries = json.loads(example_block)
    assert isinstance(entries, list) and len(entries) == 1
    entry = entries[0]
    assert isinstance(entry, dict)
    return entry


def _first_json_block(guide: str) -> str:
    match = JSON_BLOCK.search(guide)
    assert match is not None, "the guide has no json block"
    return match.group(1)


def _section(guide: str, heading: str) -> str:
    assert f"\n## {heading}\n" in guide, f"no {heading} section"
    return guide.split(f"\n## {heading}\n", 1)[1].split("\n## ", 1)[0]


def _check_line_limit(guide: str) -> None:
    count = len(guide.splitlines())
    assert count <= MAX_LINES, f"{count} lines"


def _check_steps(guide: str) -> None:
    numbers = STEP.findall(_section(guide, "Setup steps"))
    assert 1 <= len(numbers) <= MAX_SETUP_STEPS, f"step count {len(numbers)}"
    assert numbers == [str(n) for n in range(1, len(numbers) + 1)], f"step numbers {numbers}"


def _check_no_confirm(example: dict[str, Any]) -> None:
    tools = set(example["tools"])
    no_confirm = set(example.get("no_confirm", []))
    assert no_confirm <= tools, f"no_confirm {sorted(no_confirm)} not in tools {sorted(tools)}"


def _check_settings(guide: str) -> None:
    prefix = Settings.model_config["env_prefix"]
    for field in REQUIRED_SETTINGS:
        assert field in Settings.model_fields
        assert prefix + field.upper() in guide, f"missing {prefix + field.upper()}"
    for secret in SECRET_TOKEN.findall(guide):
        assert SECRET_VAR.fullmatch(secret), f"bad secret name {secret}"
    known = {prefix + name.upper() for name in Settings.model_fields}
    unknown = set(OTA_TOKEN.findall(guide)) - known
    assert not unknown, f"unknown settings: {sorted(unknown)}"


def _check_absent(guide: str, value: str) -> None:
    assert value not in guide, f"invented n8n identifier {value}"


def test_guide_line_limit(guide: str) -> None:
    _check_line_limit(guide)


def test_guide_setup_steps(guide: str) -> None:
    _check_steps(guide)


def test_example_loads(example_block: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "servers.json"
    path.write_text(example_block, encoding="utf-8")
    monkeypatch.setenv("MCP_SECRET_N8N", "x")
    servers = load_servers(str(path))
    assert len(servers) == 1
    server = servers[0]
    assert server.name == "n8n"
    assert server.transport == "http"
    assert server.url.startswith("https://n8n.example.com")
    assert server.headers == {"Authorization": "MCP_SECRET_N8N"}
    assert server.tools


def test_example_pins_and_no_confirm(example: dict[str, Any]) -> None:
    assert all(PIN.fullmatch(pin) for pin in example["tools"].values())
    _check_no_confirm(example)


def test_guide_setting_names_exist(guide: str) -> None:
    _check_settings(guide)


@pytest.mark.parametrize("value", DENYLIST)
def test_guide_has_no_invented_n8n_identifiers(guide: str, value: str) -> None:
    _check_absent(guide, value)


@pytest.mark.parametrize("value", REQUIRED_TEXT)
def test_guide_names_required_text(guide: str, value: str) -> None:
    assert value in guide


# ---- The checks catch a broken guide (acceptance scenarios of T2) ----


def test_line_check_rejects_121_lines(guide: str) -> None:
    long = guide + "x\n" * (MAX_LINES + 1 - len(guide.splitlines()))
    assert long != guide
    with pytest.raises(AssertionError, match="121 lines"):
        _check_line_limit(long)


def test_step_check_rejects_a_numbering_gap(guide: str) -> None:
    gapped = guide.replace("\n3. ", "\n4. ", 1)
    assert gapped != guide
    with pytest.raises(AssertionError, match="step numbers"):
        _check_steps(gapped)


def test_step_check_rejects_seven_steps(guide: str) -> None:
    seven = guide.replace("\n\nThe servers file for step 2", "\n7. One more.\n\nThe servers", 1)
    assert seven != guide
    with pytest.raises(AssertionError, match="step count 7"):
        _check_steps(seven)


def test_settings_check_rejects_a_typo(guide: str) -> None:
    typo = guide.replace("OTA_MCP_SERVERS_FILE", "OTA_MCP_SERVER_FILE")
    assert typo != guide
    with pytest.raises(AssertionError, match="OTA_MCP_SERVER"):
        _check_settings(typo)


def test_denylist_check_rejects_an_invented_identifier(guide: str) -> None:
    invented = guide + "\nAdd the mcpTrigger node.\n"
    assert invented != guide
    with pytest.raises(AssertionError, match="mcpTrigger"):
        _check_absent(invented, "mcpTrigger")


def test_no_confirm_check_rejects_an_unknown_tool(example: dict[str, Any]) -> None:
    ghost = {**example, "no_confirm": ["ghost_tool"]}
    assert ghost != example
    with pytest.raises(AssertionError, match="ghost_tool"):
        _check_no_confirm(ghost)


# ---- Structure of the guide ----


def test_troubleshooting_names_each_symptom(guide: str) -> None:
    items = [line for line in _section(guide, "Troubleshooting").splitlines() if line[:2] == "- "]
    for symptom in SYMPTOMS:
        assert any(symptom in item for item in items), symptom


def test_allowed_hosts_example_matches_the_url_host(guide: str, example: dict[str, Any]) -> None:
    match = ALLOWED_HOSTS_LINE.search(guide)
    assert match is not None, "no OTA_MCP_ALLOWED_HOSTS line"
    assert match.group(1).split(",") == [urlsplit(example["url"]).hostname]


def test_guide_says_allowed_hosts_is_comma_separated(guide: str) -> None:
    assert "comma-separated" in guide


def test_guide_warns_that_allowlisted_hosts_accept_plain_http(guide: str) -> None:
    assert "plain `http`" in guide
    assert "Use `https`" in guide


def test_guide_warns_against_auth_none_for_side_effects(guide: str) -> None:
    assert re.search(r"Never use auth \"none\" for tools with side effects", guide)


def test_readme_links_the_guide_once() -> None:
    assert README_PATH.read_text(encoding="utf-8").count("guides/mcp-servers.md") == 1

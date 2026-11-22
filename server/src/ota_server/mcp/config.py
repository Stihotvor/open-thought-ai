"""The MCP servers config file (D8): a JSON list of servers, each entry checked on its own.

A bad file gives no servers; a bad entry is skipped with a logged error and good entries
survive (E6). The file holds no secrets (T8).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, TypeGuard
from urllib.parse import urlsplit

from ota_server.hub.types import SEP, TOOL_NAME

log = logging.getLogger(__name__)

TRANSPORTS = ("http", "sse", "stdio")
REMOTE = ("http", "sse")
DEFAULT_TIMEOUT = 10.0
MIN_TIMEOUT = 1.0
MAX_TIMEOUT = 60.0
PIN = re.compile(r"sha256:[0-9a-f]{64}")
SECRET_VAR = re.compile(r"MCP_SECRET_[A-Z0-9_]+")
# A substring match on the upper-cased env key: "API_CREDENTIALS" holds "CRED".
SECRET_ENV_WORDS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "AUTH",
    "CRED",
    "COOKIE",
    "PRIVATE",
)
# "device" is the namespace of the built-in device tools; an MCP server with that name
# would shadow them in the hub.
RESERVED_NAMES = frozenset({"device"})


@dataclass(frozen=True)
class McpServerConfig:
    name: str
    transport: str
    url: str = ""
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    timeout: float = DEFAULT_TIMEOUT
    tools: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    no_confirm: list[str] = field(default_factory=list)


def load_servers(path: str) -> list[McpServerConfig]:
    """Read the config file at path. An empty path, a bad file or a non-list gives []."""
    if not path:
        return []
    entries = _read_list(path)
    servers: list[McpServerConfig] = []
    used: dict[str, str] = {}  # MCP_SECRET_* name to the entry that uses it (T9)
    for index, entry in enumerate(entries):
        server = _try_entry(index, entry, {s.name for s in servers}, used)
        if server is not None:
            servers.append(server)
            used.update(dict.fromkeys(server.headers.values(), server.name))
    return servers


def _read_list(path: str) -> list[Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        log.error("mcp config %s: cannot read: %s", path, exc)
        return []
    if not isinstance(data, list):
        log.error("mcp config %s: the top level must be a JSON list", path)
        return []
    return data


def _try_entry(
    index: int, entry: Any, seen: set[str], used: Mapping[str, str]
) -> McpServerConfig | None:
    """Parse and check one entry; log and return None on error. Does not change used."""
    name = entry.get("name") if isinstance(entry, dict) else None
    try:
        server = _parse_entry(entry)
        if server.name in seen:
            raise ValueError("duplicate name")
        _check_secret_reuse(server, used)
    except ValueError as exc:
        log.error("mcp config entry %d (name %r) skipped: %s", index, name, exc)
        return None
    return server


def _check_secret_reuse(server: McpServerConfig, used: Mapping[str, str]) -> None:
    """An MCP_SECRET_* name is bound to one entry: the first accepted entry wins (T9)."""
    for header, var in server.headers.items():
        if var in used:
            raise ValueError(f"header {header!r}: {var} is already used by {used[var]!r}")


def _parse_entry(entry: Any) -> McpServerConfig:
    if not isinstance(entry, dict):
        raise ValueError("entry must be a JSON object")
    name = _check_name(entry.get("name"))
    transport = entry.get("transport")
    if transport not in TRANSPORTS:
        raise ValueError(f"transport must be one of {TRANSPORTS}")
    timeout = _check_timeout(entry.get("timeout", DEFAULT_TIMEOUT))
    tools = _check_tools(name, entry.get("tools"))
    headers = _check_headers(entry)
    no_confirm = _check_no_confirm(entry.get("no_confirm", []), tools)
    if transport in REMOTE:
        if "env" in entry:
            raise ValueError("env is allowed for stdio only")
        url = _check_url(entry.get("url"))
        return McpServerConfig(
            name,
            transport,
            url=url,
            timeout=timeout,
            tools=tools,
            headers=headers,
            no_confirm=no_confirm,
        )
    command, args, env = _check_stdio(entry)
    return McpServerConfig(
        name,
        transport,
        command=command,
        args=args,
        env=env,
        timeout=timeout,
        tools=tools,
        no_confirm=no_confirm,
    )


def _check_name(name: Any) -> str:
    if not isinstance(name, str) or not TOOL_NAME.fullmatch(name):
        raise ValueError("name must match [a-zA-Z0-9_-]{1,64}")
    if SEP in name:
        raise ValueError(f"name must not contain {SEP!r}")
    if name in RESERVED_NAMES:
        raise ValueError(f"name {name!r} is reserved")
    return name


def _check_timeout(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("timeout must be a number")
    if not MIN_TIMEOUT <= value <= MAX_TIMEOUT:
        raise ValueError(f"timeout must be {MIN_TIMEOUT:g} to {MAX_TIMEOUT:g} seconds")
    return float(value)


def _check_url(url: Any) -> str:
    if not isinstance(url, str):
        raise ValueError("url is required for http and sse")
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise ValueError("url is not valid") from exc
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("url must be http or https with a host")
    if parts.username is not None or parts.password is not None:
        raise ValueError("url must not hold user info")
    if parts.query:
        raise ValueError("url must not hold a query string")
    return url


def _check_stdio(entry: dict[str, Any]) -> tuple[str, list[str], dict[str, str]]:
    command = entry.get("command")
    if not isinstance(command, str) or not command:
        raise ValueError("command is required for stdio")
    args = entry.get("args")
    if not _is_str_list(args):
        raise ValueError("args must be a list of strings for stdio")
    env = entry.get("env", {})
    if not isinstance(env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    ):
        raise ValueError("env must map strings to strings")
    _check_env_secrets(env)
    return command, list(args), dict(env)


def _check_env_secrets(env: dict[str, str]) -> None:
    """A stdio env must not carry a secret (T10). The message names the key, never the value."""
    for key, value in env.items():
        if any(word in key.upper() for word in SECRET_ENV_WORDS):
            raise ValueError(f"env key {key!r} looks like a secret")
        if value.startswith("MCP_SECRET_"):
            raise ValueError(f"env key {key!r} refers to an MCP_SECRET_ variable")


def _check_headers(entry: dict[str, Any]) -> dict[str, str]:
    """Headers map a header name to an MCP_SECRET_* variable name, http and sse only (T9, E6)."""
    if "headers" not in entry:
        return {}
    if entry.get("transport") not in REMOTE:
        raise ValueError("headers are allowed for http and sse only")
    headers = entry["headers"]
    if not isinstance(headers, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
    ):
        raise ValueError("headers must map strings to strings")
    for header, var in headers.items():
        if not SECRET_VAR.fullmatch(var):
            raise ValueError(f"header {header!r} must name an MCP_SECRET_* variable")
    return dict(headers)


def _check_tools(server: str, tools: Any) -> dict[str, str]:
    if not isinstance(tools, dict):
        raise ValueError("tools must be a map of tool name to sha256 pin")
    kept: dict[str, str] = {}
    for tool, pin in tools.items():
        if not isinstance(pin, str) or not PIN.fullmatch(pin):
            raise ValueError(f"tool {tool!r}: pin must be sha256:<64 hex>")
        if TOOL_NAME.fullmatch(tool):
            kept[tool] = pin
        else:
            log.error("mcp config server %r: tool name %r dropped (bad name)", server, tool)
    return kept


def _check_no_confirm(no_confirm: Any, tools: Mapping[str, str]) -> list[str]:
    """no_confirm opts allowlisted tools out of the spoken yes (D10); unknown names reject."""
    if not _is_str_list(no_confirm):
        raise ValueError("no_confirm must be a list of strings")
    for tool in no_confirm:
        if tool not in tools:
            raise ValueError(f"no_confirm: {tool!r} is not a key of tools")
    return list(no_confirm)


def _is_str_list(value: Any) -> TypeGuard[list[str]]:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)

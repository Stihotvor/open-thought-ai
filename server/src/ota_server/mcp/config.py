"""The MCP servers config file (D8): a JSON list of servers, each entry checked on its own.

A bad file gives no servers; a bad entry is skipped with a logged error and good entries
survive (E6). The file holds no secrets (T8).
"""

from __future__ import annotations

import json
import logging
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


@dataclass(frozen=True)
class McpServerConfig:
    name: str
    transport: str
    url: str = ""
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    timeout: float = DEFAULT_TIMEOUT
    tools: list[str] = field(default_factory=list)


def load_servers(path: str) -> list[McpServerConfig]:
    """Read the config file at path. An empty path, a bad file or a non-list gives []."""
    if not path:
        return []
    entries = _read_list(path)
    servers: list[McpServerConfig] = []
    for index, entry in enumerate(entries):
        server = _try_entry(index, entry, {s.name for s in servers})
        if server is not None:
            servers.append(server)
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


def _try_entry(index: int, entry: Any, seen: set[str]) -> McpServerConfig | None:
    name = entry.get("name") if isinstance(entry, dict) else None
    try:
        server = _parse_entry(entry)
        if server.name in seen:
            raise ValueError("duplicate name")
    except ValueError as exc:
        log.error("mcp config entry %d (name %r) skipped: %s", index, name, exc)
        return None
    return server


def _parse_entry(entry: Any) -> McpServerConfig:
    if not isinstance(entry, dict):
        raise ValueError("entry must be a JSON object")
    name = _check_name(entry.get("name"))
    transport = entry.get("transport")
    if transport not in TRANSPORTS:
        raise ValueError(f"transport must be one of {TRANSPORTS}")
    timeout = _check_timeout(entry.get("timeout", DEFAULT_TIMEOUT))
    tools = _check_tools(name, entry.get("tools"))
    if transport in REMOTE:
        if "env" in entry:
            raise ValueError("env is allowed for stdio only")
        url = _check_url(entry.get("url"))
        return McpServerConfig(name, transport, url=url, timeout=timeout, tools=tools)
    command, args, env = _check_stdio(entry)
    return McpServerConfig(
        name, transport, command=command, args=args, env=env, timeout=timeout, tools=tools
    )


def _check_name(name: Any) -> str:
    if not isinstance(name, str) or not TOOL_NAME.fullmatch(name):
        raise ValueError("name must match [a-zA-Z0-9_-]{1,64}")
    if SEP in name:
        raise ValueError(f"name must not contain {SEP!r}")
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
    return command, list(args), dict(env)


def _check_tools(server: str, tools: Any) -> list[str]:
    if not _is_str_list(tools):
        raise ValueError("tools must be a list of strings")
    kept: list[str] = []
    for tool in tools:
        if TOOL_NAME.fullmatch(tool):
            kept.append(tool)
        else:
            log.error("mcp config server %r: tool name %r dropped (bad name)", server, tool)
    return kept


def _is_str_list(value: Any) -> TypeGuard[list[str]]:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)

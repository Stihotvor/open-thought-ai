"""Shared helpers for the tool-safety QA tests (docs/design/tool-safety/qa/cases.md)."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from mcp_fakes import AlwaysRedirectServer, _read_request, build_fake

from ota_server.config import Settings
from ota_server.hub.types import CallCtx, ToolCall, ToolOutcome
from ota_server.mcp.config import McpServerConfig
from ota_server.mcp.port import RawResult, RawTool, Verifier
from ota_server.mcp.ssrf import Resolver
from ota_server.mcp.toolfilter import tool_hash

LOOPBACK = "127.0.0.1"
PUBLIC = "203.0.113.10"
PIN0 = "sha256:" + "0" * 64
EMPTY: frozenset[str] = frozenset()


class FakeResolver:
    """A fake DNS: a table of host to IPs, an optional error, a record of each lookup."""

    def __init__(self, table: dict[str, list[str]] | None = None, raises: Exception | None = None):
        self.table = table or {}
        self.raises = raises
        self.calls: list[str] = []

    async def __call__(self, host: str) -> list[str]:
        self.calls.append(host)
        if self.raises is not None:
            raise self.raises
        return self.table.get(host, [])


def as_resolver(resolver: FakeResolver) -> Resolver:
    return resolver


def remote(
    url: str, headers: dict[str, str] | None = None, name: str = "srv", transport: str = "http"
) -> McpServerConfig:
    return McpServerConfig(name, transport, url=url, headers=headers or {})


def raw(
    name: str = "echo", description: str = "Echo text", schema: dict[str, Any] | None = None
) -> RawTool:
    if schema is None:
        schema = {"type": "object", "properties": {"text": {"type": "string"}}}
    return RawTool(name, description, schema)


class FakeConn:
    """An in-memory McpConnection: a fixed tool list, calls echo their text."""

    def __init__(self, tools: list[RawTool]) -> None:
        self.tools = tools
        self.calls: list[str] = []
        self.verify: Verifier | None = None

    def set_verifier(self, verify: Verifier) -> None:
        self.verify = verify

    async def list_tools(self) -> list[RawTool]:
        return list(self.tools)

    async def call_tool(self, name: str, args: dict[str, Any]) -> RawResult:
        self.calls.append(name)
        return RawResult(False, [("text", str(args.get("text", "")))])

    async def close(self) -> None:
        return None


async def fake_pins() -> dict[str, str]:
    """The owner's pins for the tools of build_fake (echo and hidden)."""
    tools = await build_fake().list_tools()
    return {t.name: tool_hash(RawTool(t.name, t.description or "", t.inputSchema)) for t in tools}


def settings_for(
    tmp_path: Path, entries: list[dict[str, Any]], allowed: str = LOOPBACK
) -> Settings:
    path = tmp_path / "servers.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return Settings(
        _env_file=None,  # type: ignore[call-arg]  # pydantic-settings init option
        device_id="test",
        device_token_sha256=hashlib.sha256(b"token").hexdigest(),
        mcp_servers_file=str(path),
        mcp_allowed_hosts=allowed,
    )


async def run_tool(hub: Any, name: str, args: dict[str, Any] | None = None) -> ToolOutcome:
    outcome: ToolOutcome = await hub.run(ToolCall("c1", name, args or {}), CallCtx())
    return outcome


class ReplyServer(AlwaysRedirectServer):
    """A tiny HTTP server on 127.0.0.1 that answers every request with one fixed status."""

    def __init__(self, status: int, headers: dict[str, str] | None = None, body: bytes = b""):
        super().__init__()
        self.status = status
        self.extra = headers or {}
        self.body = body

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await _read_request(reader)
            self.requests += 1
            lines = [f"HTTP/1.1 {self.status} Reply", f"Content-Length: {len(self.body)}"]
            lines += [f"{k}: {v}" for k, v in self.extra.items()] + ["Connection: close", "", ""]
            writer.write("\r\n".join(lines).encode("ascii") + self.body)
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

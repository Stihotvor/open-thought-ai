"""T5: SafeConnectionPolicy (D2, T5, T6, T9, T12, E7, F2) with a fake resolver and environ."""

import asyncio
import logging

import pytest

from ota_server.mcp.config import McpServerConfig
from ota_server.mcp.hooks import ConnectionDenied, ConnectionPolicy
from ota_server.mcp.policy import SafeConnectionPolicy

PUBLIC = "93.184.216.34"
EMPTY: frozenset[str] = frozenset()


class FakeResolver:
    """A resolver that reads a dict, records each call, and may raise."""

    def __init__(self, table: dict[str, list[str]], raises: Exception | None = None) -> None:
        self.table = table
        self.raises = raises
        self.calls: list[str] = []

    async def __call__(self, host: str) -> list[str]:
        self.calls.append(host)
        if self.raises is not None:
            raise self.raises
        return self.table.get(host, [])


def _remote(url: str, headers: dict[str, str] | None = None) -> McpServerConfig:
    return McpServerConfig("srv", "http", url=url, headers=headers or {})


def _policy(
    resolver: FakeResolver,
    allowed: frozenset[str] = EMPTY,
    environ: dict[str, str] | None = None,
) -> SafeConnectionPolicy:
    return SafeConnectionPolicy(allowed, resolver, lambda: environ or {})


def _connect(policy: SafeConnectionPolicy, config: McpServerConfig) -> dict[str, str]:
    return asyncio.run(policy.before_connect(config))


def test_policy_satisfies_protocol() -> None:
    policy: ConnectionPolicy = _policy(FakeResolver({}))
    assert policy is not None


def test_https_public_host_returns_empty() -> None:
    policy = _policy(FakeResolver({"a.example.com": [PUBLIC]}))
    assert _connect(policy, _remote("https://a.example.com/mcp")) == {}


def test_http_public_host_denied() -> None:
    policy = _policy(FakeResolver({"a.example.com": [PUBLIC]}))
    with pytest.raises(ConnectionDenied, match="http needs https"):
        _connect(policy, _remote("http://a.example.com/mcp"))


@pytest.mark.parametrize(
    ("host", "allowed"),
    [
        ("127.0.0.1", frozenset({"n8n", "127.0.0.1"})),
        ("n8n", frozenset({"n8n"})),
    ],
)
def test_http_loopback_or_allowlisted_passes(host: str, allowed: frozenset[str]) -> None:
    policy = _policy(FakeResolver({}), allowed)
    assert _connect(policy, _remote(f"http://{host}:5678/mcp")) == {}


def test_http_loopback_not_allowlisted_denied() -> None:
    policy = _policy(FakeResolver({}), frozenset({"n8n"}))
    with pytest.raises(ConnectionDenied, match="loopback"):
        _connect(policy, _remote("http://127.0.0.1:5678/mcp"))


def test_blocked_ip_denied_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    policy = _policy(FakeResolver({"a.example.com": ["10.0.0.9"]}))
    with caplog.at_level(logging.WARNING), pytest.raises(ConnectionDenied):
        _connect(policy, _remote("https://a.example.com/mcp"))
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    text = warnings[0].getMessage()
    assert "srv" in text and "a.example.com" in text and "blocked address" in text


def test_secret_header_from_environ(caplog: pytest.LogCaptureFixture) -> None:
    policy = _policy(
        FakeResolver({"a.example.com": [PUBLIC]}), environ={"MCP_SECRET_N8N": "s3cr3t"}
    )
    config = _remote("https://a.example.com/mcp", {"X-Api": "MCP_SECRET_N8N"})
    with caplog.at_level(logging.DEBUG):
        assert _connect(policy, config) == {"X-Api": "s3cr3t"}
    assert all("s3cr3t" not in r.getMessage() for r in caplog.records)


def test_missing_variable_denied(caplog: pytest.LogCaptureFixture) -> None:
    policy = _policy(FakeResolver({"a.example.com": [PUBLIC]}), environ={})
    config = _remote("https://a.example.com/mcp", {"X-Api": "MCP_SECRET_N8N"})
    with caplog.at_level(logging.WARNING), pytest.raises(ConnectionDenied):
        _connect(policy, config)
    assert any("MCP_SECRET_N8N" in r.getMessage() for r in caplog.records)


def test_empty_variable_denied() -> None:
    policy = _policy(FakeResolver({"a.example.com": [PUBLIC]}), environ={"MCP_SECRET_N8N": ""})
    config = _remote("https://a.example.com/mcp", {"X-Api": "MCP_SECRET_N8N"})
    with pytest.raises(ConnectionDenied, match="missing variable MCP_SECRET_N8N"):
        _connect(policy, config)


def test_stdio_returns_empty_without_resolving() -> None:
    resolver = FakeResolver({})
    config = McpServerConfig("local", "stdio", command="python")
    assert _connect(_policy(resolver), config) == {}
    assert resolver.calls == []


def test_recheck_on_reconnect() -> None:
    resolver = FakeResolver({"a.example.com": [PUBLIC]})
    policy = _policy(resolver)
    config = _remote("https://a.example.com/mcp")
    assert _connect(policy, config) == {}
    resolver.table["a.example.com"] = ["10.0.0.9"]
    with pytest.raises(ConnectionDenied):
        _connect(policy, config)


def test_dns_failure_denied_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    policy = _policy(FakeResolver({}, raises=OSError("no such host")))
    with caplog.at_level(logging.WARNING), pytest.raises(ConnectionDenied, match="dns"):
        _connect(policy, _remote("https://a.example.com/mcp"))
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_rotated_secret_applies_on_next_connect() -> None:
    env = {"MCP_SECRET_N8N": "old"}
    policy = SafeConnectionPolicy(EMPTY, FakeResolver({"a.example.com": [PUBLIC]}), lambda: env)
    config = _remote("https://a.example.com/mcp", {"Authorization": "MCP_SECRET_N8N"})
    assert _connect(policy, config) == {"Authorization": "old"}
    env["MCP_SECRET_N8N"] = "new"
    assert _connect(policy, config) == {"Authorization": "new"}

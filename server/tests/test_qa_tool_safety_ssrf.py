"""QA cases 001-021 (SSRF and scheme) through SafeConnectionPolicy with a fake resolver."""

import asyncio
import logging
from urllib.parse import urlsplit

import pytest
from qa_support import EMPTY, PUBLIC, FakeResolver, as_resolver, remote

from ota_server.mcp.hooks import ConnectionDenied
from ota_server.mcp.policy import SafeConnectionPolicy

DENIED = [
    ("qa_001", "https://127.0.0.1:9000/mcp", {}, EMPTY, "loopback"),
    ("qa_002", "https://[::1]:9000/mcp", {}, EMPTY, "loopback"),
    ("qa_003", "https://169.254.169.254/latest", {}, EMPTY, "link-local"),
    ("qa_004", "https://tools.example/mcp", {"tools.example": ["10.1.2.3"]}, EMPTY, "private"),
    ("qa_005", "https://tools.example/mcp", {"tools.example": ["172.20.0.5"]}, EMPTY, "private"),
    ("qa_006", "https://tools.example/mcp", {"tools.example": ["192.168.1.50"]}, EMPTY, "private"),
    ("qa_007", "https://tools.example/mcp", {"tools.example": ["fd12:3456::1"]}, EMPTY, "private"),
    ("qa_008", "https://0.0.0.0:9000/mcp", {}, EMPTY, "unspecified"),
    ("qa_009", "https://tools.example/mcp", {"tools.example": ["224.0.0.251"]}, EMPTY, "multicast"),
    ("qa_010", "https://n8n:5678/mcp", {"n8n": ["203.0.113.7"]}, EMPTY, "single-label"),
    (
        "qa_014",
        "https://192.168.1.51/mcp",
        {},
        frozenset({"192.168.1.50"}),
        "private",
    ),
    (
        "qa_016",
        "https://n8n.evil.example/mcp",
        {"n8n.evil.example": ["10.0.0.9"]},
        frozenset({"n8n"}),
        "private",
    ),
    (
        "qa_017",
        "https://dual.example/mcp",
        {"dual.example": [PUBLIC, "10.0.0.5"]},
        EMPTY,
        "private",
    ),
    ("qa_018", "https://[::ffff:10.0.0.1]/mcp", {}, EMPTY, "private"),
    ("qa_019", "https://map.example/mcp", {"map.example": ["::ffff:127.0.0.1"]}, EMPTY, "loopback"),
    ("qa_021", "https://empty.example/mcp", {"empty.example": []}, EMPTY, "dns"),
]


@pytest.mark.parametrize(
    ("case", "url", "table", "allowed", "needle"), DENIED, ids=[d[0] for d in DENIED]
)
def test_qa_denied_with_logged_reason(
    caplog: pytest.LogCaptureFixture,
    case: str,
    url: str,
    table: dict[str, list[str]],
    allowed: frozenset[str],
    needle: str,
) -> None:
    resolver = FakeResolver(table)
    policy = SafeConnectionPolicy(allowed, as_resolver(resolver), lambda: {})
    with caplog.at_level(logging.WARNING), pytest.raises(ConnectionDenied):
        asyncio.run(policy.before_connect(remote(url)))
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    host = (urlsplit(url).hostname or "").lower()
    assert "srv" in warnings[0] and host in warnings[0]
    assert needle in warnings[0], f"{case}: reason in log was: {warnings[0]}"
    if case == "qa_003":
        assert resolver.calls == []  # no lookup, so no request reaches the fake network


ALLOWED = [
    # The case names 203.0.113.10, but Python treats TEST-NET-3 as private, so a routable
    # public address stands in for it.
    (
        "qa_012_026",
        "https://tools.example.org/mcp",
        {"tools.example.org": ["93.184.216.34"]},
        EMPTY,
    ),
    ("qa_013", "https://192.168.1.50/mcp", {}, frozenset({"192.168.1.50"})),
    ("qa_015", "https://N8N:5678/mcp", {}, frozenset({"n8n"})),
    ("qa_025", "http://n8n:5678/mcp", {}, frozenset({"n8n"})),
]


@pytest.mark.parametrize(("case", "url", "table", "allowed"), ALLOWED, ids=[a[0] for a in ALLOWED])
def test_qa_allowed_without_denial(
    caplog: pytest.LogCaptureFixture,
    case: str,
    url: str,
    table: dict[str, list[str]],
    allowed: frozenset[str],
) -> None:
    resolver = FakeResolver(table)
    policy = SafeConnectionPolicy(allowed, as_resolver(resolver), lambda: {})
    with caplog.at_level(logging.DEBUG):
        assert asyncio.run(policy.before_connect(remote(url))) == {}
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    if allowed:
        assert resolver.calls == []  # an allowlisted host skips the IP check


def test_qa_023_http_public_host_is_a_scheme_denial(caplog: pytest.LogCaptureFixture) -> None:
    policy = SafeConnectionPolicy(
        EMPTY, as_resolver(FakeResolver({"t.example.org": [PUBLIC]})), lambda: {}
    )
    with caplog.at_level(logging.WARNING), pytest.raises(ConnectionDenied, match="https"):
        asyncio.run(policy.before_connect(remote("http://t.example.org/mcp")))
    assert any("t.example.org" in r.getMessage() for r in caplog.records)


def test_qa_024_loopback_http_passes_only_when_allowlisted() -> None:
    url = "http://127.0.0.1:9000/mcp"
    allowed = SafeConnectionPolicy(
        frozenset({"127.0.0.1"}), as_resolver(FakeResolver()), lambda: {}
    )
    assert asyncio.run(allowed.before_connect(remote(url))) == {}
    bare = SafeConnectionPolicy(EMPTY, as_resolver(FakeResolver()), lambda: {})
    with pytest.raises(ConnectionDenied, match="loopback"):
        asyncio.run(bare.before_connect(remote(url)))

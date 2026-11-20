"""T1: ssrf.check_host with a fake resolver (D1, M1, T6, E1, E2, E5, E7)."""

import asyncio
import ipaddress

import pytest

from ota_server.mcp import ssrf
from ota_server.mcp.ssrf import Resolver, check_host

EMPTY: frozenset[str] = frozenset()


class FakeResolver:
    """A resolver that reads a dict and records each call."""

    def __init__(self, table: dict[str, list[str]], raises: Exception | None = None) -> None:
        self.table = table
        self.raises = raises
        self.calls: list[str] = []

    async def __call__(self, host: str) -> list[str]:
        self.calls.append(host)
        if self.raises is not None:
            raise self.raises
        return self.table.get(host, [])


def _fake(table: dict[str, list[str]]) -> Resolver:
    async def fake(host: str) -> list[str]:
        return table.get(host, [])

    return fake


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "169.254.169.254",
        "10.1.2.3",
        "172.16.0.5",
        "192.168.1.1",
        "fc00::1",
        "0.0.0.0",
        "224.0.0.1",
    ],
)
def test_blocked_address_ranges(ip: str) -> None:
    reason = asyncio.run(check_host("svc.example.com", EMPTY, _fake({"svc.example.com": [ip]})))
    assert reason


@pytest.mark.parametrize(
    "ip",
    [
        "10.0.0.0",
        "10.255.255.255",
        "172.16.0.0",
        "172.31.255.255",
        "192.168.0.0",
        "fc00::",
        "fdff::1",
    ],
)
def test_is_private_covers_each_range(ip: str) -> None:
    assert ipaddress.ip_address(ip).is_private


def test_single_label_name_denied_without_resolving() -> None:
    resolver = FakeResolver({"n8n": ["93.184.216.34"]})
    reason = asyncio.run(check_host("n8n", EMPTY, resolver))
    assert reason == "single-label host"
    assert resolver.calls == []


def test_ipv6_literal_is_not_single_label() -> None:
    resolver = FakeResolver({})
    reason = asyncio.run(check_host("2001:db8::1", EMPTY, resolver))
    assert reason != "single-label host"
    blocked = ipaddress.ip_address("2001:db8::1").is_private
    assert bool(reason) == blocked
    assert resolver.calls == []


def test_ipv4_mapped_ipv6_is_checked_as_ipv4() -> None:
    reason = asyncio.run(
        check_host("a.example.com", EMPTY, _fake({"a.example.com": ["::ffff:10.0.0.1"]}))
    )
    assert reason == "blocked address (private)"


def test_mixed_public_and_private_ips_denied() -> None:
    resolver = _fake({"a.example.com": ["93.184.216.34", "10.0.0.9"]})
    reason = asyncio.run(check_host("a.example.com", EMPTY, resolver))
    assert reason


def test_public_host_allowed() -> None:
    reason = asyncio.run(
        check_host("a.example.com", EMPTY, _fake({"a.example.com": ["93.184.216.34"]}))
    )
    assert reason is None


def test_allowlist_bypass_is_case_insensitive() -> None:
    resolver = FakeResolver({})
    reason = asyncio.run(check_host("N8N", frozenset({"n8n"}), resolver))
    assert reason is None
    assert resolver.calls == []


def test_resolver_error_is_deny() -> None:
    resolver = FakeResolver({}, raises=OSError("no such host"))
    reason = asyncio.run(check_host("a.example.com", EMPTY, resolver))
    assert reason == "dns failure"


def test_empty_resolution_is_deny() -> None:
    reason = asyncio.run(check_host("a.example.com", EMPTY, _fake({})))
    assert reason == "dns failure"


@pytest.mark.parametrize("ip", ["0.0.0.0", "::"])
def test_unspecified_reason_is_specific(ip: str) -> None:
    reason = asyncio.run(check_host("svc.example.com", EMPTY, _fake({"svc.example.com": [ip]})))
    assert reason == "blocked address (unspecified)"


@pytest.mark.parametrize("ip", ["100.64.0.1", "100.100.100.200", "64:ff9b::a9fe:a9fe", "240.0.0.1"])
def test_non_global_and_wrapped_addresses_denied(ip: str) -> None:
    reason = asyncio.run(check_host("svc.example.com", EMPTY, _fake({"svc.example.com": [ip]})))
    assert reason


@pytest.mark.parametrize("ip", ["100.64.0.1", "100.100.100.200"])
def test_shared_address_space_reason_is_non_global(ip: str) -> None:
    reason = asyncio.run(check_host("svc.example.com", EMPTY, _fake({"svc.example.com": [ip]})))
    assert reason == "blocked address (non-global)"


@pytest.mark.parametrize("ip", ["64:ff9b::a9fe:a9fe", "2002:a9fe:a9fe::1"])
def test_nat64_and_6to4_are_checked_as_ipv4(ip: str) -> None:
    reason = asyncio.run(check_host("svc.example.com", EMPTY, _fake({"svc.example.com": [ip]})))
    assert reason == "blocked address (link-local)"


def test_allowlist_bypasses_non_global() -> None:
    resolver = FakeResolver({"cgnat.example.com": ["100.64.0.1"]})
    reason = asyncio.run(
        check_host("cgnat.example.com", frozenset({"cgnat.example.com"}), resolver)
    )
    assert reason is None


def test_slow_dns_is_deny(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssrf, "DNS_TIMEOUT", 0.01)

    async def slow_getaddrinfo(*_args: object, **_kwargs: object) -> list[object]:
        await asyncio.sleep(5)
        return []

    async def run() -> str | None:
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "getaddrinfo", slow_getaddrinfo)
        return await check_host("slow.example.com", EMPTY)

    assert asyncio.run(run()) == "dns failure"


def test_dns_timeout_constant() -> None:
    assert ssrf.DNS_TIMEOUT == 3.0


@pytest.mark.parametrize(
    ("host", "allowed", "expected"),
    [
        ("127.0.0.1", EMPTY, True),
        ("::1", EMPTY, True),
        ("n8n.lan", frozenset({"n8n.lan"}), True),
        ("N8N.LAN", frozenset({"n8n.lan"}), True),
        ("n8n.lan", EMPTY, False),
        ("localhost", EMPTY, False),
        ("10.0.0.1", EMPTY, False),
    ],
)
def test_plain_http_allowed(host: str, allowed: frozenset[str], expected: bool) -> None:
    assert ssrf.plain_http_allowed(host, allowed) is expected

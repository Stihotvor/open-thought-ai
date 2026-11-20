"""SSRF host check for external MCP servers (D1).

check_host returns None when a host may be contacted, or a short deny reason. It does not
raise or log; the caller logs the denial. Stdlib only.
"""

import asyncio
import ipaddress
from collections.abc import Awaitable, Callable

Resolver = Callable[[str], Awaitable[list[str]]]

IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

_BLOCKED_CLASSES: tuple[str, ...] = (
    "loopback",
    "link_local",
    "unspecified",
    "multicast",
    "private",
)

DNS_TIMEOUT = 3.0
NAT64 = ipaddress.IPv6Network("64:ff9b::/96")


async def default_resolver(host: str) -> list[str]:
    """Resolve host with the event loop and return the unique IP strings.

    A lookup slower than DNS_TIMEOUT raises TimeoutError, which check_host treats as a
    DNS failure (deny).
    """
    async with asyncio.timeout(DNS_TIMEOUT):
        infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def _parse_ip(text: str) -> IpAddress | None:
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


def _unwrap(ip: IpAddress) -> IpAddress:
    """Return the IPv4 address embedded by IPv4-mapped, NAT64 (64:ff9b::/96) or 6to4."""
    if not isinstance(ip, ipaddress.IPv6Address):
        return ip
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip in NAT64:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if ip.sixtofour is not None:
        return ip.sixtofour
    return ip


def _blocked_class(ip: IpAddress) -> str | None:
    ip = _unwrap(ip)
    for name in _BLOCKED_CLASSES:
        if getattr(ip, f"is_{name}"):
            return name.replace("_", "-")
    if not ip.is_global:
        return "non-global"
    return None


async def _resolve(host: str, resolver: Resolver) -> list[IpAddress] | None:
    try:
        texts = await resolver(host)
    except Exception:
        return None
    ips = [_parse_ip(t) for t in texts]
    if not ips or any(ip is None for ip in ips):
        return None
    return [ip for ip in ips if ip is not None]


def plain_http_allowed(host: str, allowed_hosts: frozenset[str]) -> bool:
    """Return True if plain http may reach host: a loopback literal or an allowlisted host."""
    host = host.lower()
    if host in allowed_hosts:
        return True
    literal = _parse_ip(host)
    return literal is not None and literal.is_loopback


async def check_host(
    host: str, allowed_hosts: frozenset[str], resolver: Resolver = default_resolver
) -> str | None:
    """Return None if host is allowed, otherwise a short deny reason."""
    host = host.lower()
    if host in allowed_hosts:
        return None
    literal = _parse_ip(host)
    if literal is not None:
        ips = [literal]
    elif "." not in host:
        return "single-label host"
    else:
        resolved = await _resolve(host, resolver)
        if resolved is None:
            return "dns failure"
        ips = resolved
    for ip in ips:
        cls = _blocked_class(ip)
        if cls is not None:
            return f"blocked address ({cls})"
    return None

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
    "private",
    "unspecified",
    "multicast",
)


async def default_resolver(host: str) -> list[str]:
    """Resolve host with the event loop and return the unique IP strings."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def _parse_ip(text: str) -> IpAddress | None:
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


def _blocked_class(ip: IpAddress) -> str | None:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    for name in _BLOCKED_CLASSES:
        if getattr(ip, f"is_{name}"):
            return name.replace("_", "-")
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

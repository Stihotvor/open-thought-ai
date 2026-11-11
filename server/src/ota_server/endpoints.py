"""Model endpoint settings, one per role (ADR 0002). Read once at startup.

Environment:
- OTA_MODEL_BASE_URL, OTA_MODEL_KEY: the default endpoint and its key.
- OTA_<ROLE>_BASE_URL, OTA_<ROLE>_KEY, OTA_<ROLE>_MODEL, OTA_<ROLE>_RATE, OTA_<ROLE>_BURST
  for ROLE in CHAT, STT, TTS, MODERATION, EMBEDDINGS. A role without a model is not used.
- OTA_ALLOW_INSECURE_MODEL_ENDPOINT=1 allows http:// to a private LAN IP (logs a warning).

Key rules (docs/security.md): a role with its own base URL needs its own key. A key is only
sent to the base URL of its role. https is required unless the host is loopback.
"""

import ipaddress
import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

CHAT, STT, TTS, MODERATION, EMBEDDINGS = "chat", "stt", "tts", "moderation", "embeddings"
ROLES = (CHAT, STT, TTS, MODERATION, EMBEDDINGS)
INSECURE_FLAG = "OTA_ALLOW_INSECURE_MODEL_ENDPOINT"
MAX_BURST = 100


class ConfigError(Exception):
    """Model endpoint settings are not safe or not complete. The server must not start."""


@dataclass(frozen=True)
class RoleConfig:
    role: str
    base_url: str
    key: str = field(repr=False)
    model: str
    rate: float
    burst: int

    @property
    def host(self) -> str:
        return urlsplit(self.base_url).hostname or ""


LAN_NETWORKS = tuple(
    ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)


def _ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The IP of a literal host, with IPv4-mapped IPv6 unwrapped. None for a name."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None
    mapped = getattr(ip, "ipv4_mapped", None)
    return mapped or ip


def _is_lan(ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None) -> bool:
    """RFC 1918 or unique-local only. No link-local (metadata), no unspecified."""
    return ip is not None and any(ip in net for net in LAN_NETWORKS)


def _is_loopback(host: str) -> bool:
    ip = _ip(host)
    return host == "localhost" or (ip is not None and ip.is_loopback)


def _check_url(role: str, url: str, allow_insecure: bool) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError(f"{role}: base URL must be http(s)://host")
    if parts.username or parts.password:
        raise ConfigError(f"{role}: base URL must not contain credentials")
    if parts.query or parts.fragment:
        raise ConfigError(f"{role}: base URL must not contain a query or fragment")
    if parts.scheme == "https" or _is_loopback(parts.hostname):
        return
    if not allow_insecure or not _is_lan(_ip(parts.hostname)):
        raise ConfigError(f"{role}: http:// needs loopback, or a private IP and {INSECURE_FLAG}")
    log.warning("role %s uses http:// on host %s", role, parts.hostname)


def _number[N: (int, float)](role: str, name: str, raw: str, kind: type[N]) -> N:
    try:
        value = kind(raw)
    except ValueError:
        raise ConfigError(f"{role}: {name} is not a number") from None
    if not math.isfinite(value) or value <= 0:
        raise ConfigError(f"{role}: {name} must be finite and > 0")
    return value


def load_roles(env: Mapping[str, str]) -> dict[str, RoleConfig]:
    """Build the role configs. Raise ConfigError on any unsafe or missing value."""
    allow_insecure = env.get(INSECURE_FLAG) == "1"
    default_url = env.get("OTA_MODEL_BASE_URL", "").rstrip("/")
    default_key = env.get("OTA_MODEL_KEY", "")
    roles = {}
    for role in ROLES:
        prefix = f"OTA_{role.upper()}_"
        model = env.get(prefix + "MODEL", "")
        if not model:
            continue
        own_url = env.get(prefix + "BASE_URL", "").rstrip("/")
        if own_url:
            url, key = own_url, env.get(prefix + "KEY", "")
            if not key:
                raise ConfigError(f"{role}: own base URL needs its own key")
        else:
            url, key = default_url, env.get(prefix + "KEY", "") or default_key
        if not url:
            raise ConfigError(f"{role}: no base URL")
        _check_url(role, url, allow_insecure)
        if not key and not _is_loopback(urlsplit(url).hostname or ""):
            raise ConfigError(f"{role}: no key for a non-loopback endpoint")
        burst = _number(role, "burst", env.get(prefix + "BURST", "1"), int)
        if burst > MAX_BURST:
            raise ConfigError(f"{role}: burst must be <= {MAX_BURST}")
        roles[role] = RoleConfig(
            role=role,
            base_url=url,
            key=key,
            model=model,
            rate=_number(role, "rate", env.get(prefix + "RATE", "1"), float),
            burst=burst,
        )
    return roles

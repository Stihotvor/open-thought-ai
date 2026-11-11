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


def _ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


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
    ip = _ip(parts.hostname)
    if not allow_insecure or ip is None or not (ip.is_private or ip.is_link_local):
        raise ConfigError(f"{role}: http:// needs loopback, or a private IP and {INSECURE_FLAG}")
    log.warning("role %s uses http:// on host %s", role, parts.hostname)


def _number(role: str, name: str, raw: str, kind: type[float] | type[int]) -> float:
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
        burst = int(_number(role, "burst", env.get(prefix + "BURST", "1"), int))
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
    _check_shared_limits(roles)
    return roles


def bucket_id(cfg: RoleConfig) -> tuple[str, str]:
    """Roles with the same endpoint and key share one provider quota, so one bucket.

    Moderation always gets its own reserved bucket, so a TTS burst cannot starve it.
    """
    if cfg.role == MODERATION:
        return (MODERATION, "")
    return (cfg.base_url, cfg.key)


def _check_shared_limits(roles: Mapping[str, RoleConfig]) -> None:
    seen: dict[tuple[str, str], RoleConfig] = {}
    for cfg in roles.values():
        first = seen.setdefault(bucket_id(cfg), cfg)
        if (first.rate, first.burst) != (cfg.rate, cfg.burst):
            raise ConfigError(
                f"{cfg.role} and {first.role} share an endpoint and key, "
                "so they need the same rate and burst"
            )

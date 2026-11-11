"""Thin clients for OpenAI-compatible model endpoints, one per role (ADR 0002).

Environment, read once at startup:
- OTA_MODEL_BASE_URL, OTA_MODEL_KEY: the default endpoint and its key.
- OTA_<ROLE>_BASE_URL, OTA_<ROLE>_KEY, OTA_<ROLE>_MODEL, OTA_<ROLE>_RATE, OTA_<ROLE>_BURST
  for ROLE in CHAT, STT, TTS, MODERATION, EMBEDDINGS.
- OTA_ALLOW_INSECURE_MODEL_ENDPOINT=1 allows http:// on the LAN (logs a warning).

Key rules (docs/security.md): a role with its own base URL needs its own key. A key is only
sent to the base URL of its role. https is required unless the host is loopback.
"""

import ipaddress
import json
import logging
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from .ratelimit import Retryable429, TokenBucket, TurnBudget, limited_call

log = logging.getLogger(__name__)

ROLES = ("chat", "stt", "tts", "moderation", "embeddings")
CONNECT_TIMEOUT = 3.0


class ConfigError(Exception):
    """Model endpoint settings are not safe or not complete. The server must not start."""


class ProviderError(Exception):
    """A model call failed with an error that is not a 429."""


@dataclass(frozen=True)
class RoleConfig:
    role: str
    base_url: str
    key: str
    model: str
    rate: float
    burst: int

    @property
    def host(self) -> str:
        return urlsplit(self.base_url).hostname or ""


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _check_url(role: str, url: str, allow_insecure: bool) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError(f"{role}: base URL must be http(s)://host")
    if parts.username or parts.password:
        raise ConfigError(f"{role}: base URL must not contain credentials")
    if parts.scheme == "http" and not _is_loopback(parts.hostname):
        if not allow_insecure:
            raise ConfigError(f"{role}: http:// needs a loopback host or the insecure flag")
        log.warning("role %s uses http:// on host %s", role, parts.hostname)


def load_roles(env: Mapping[str, str]) -> dict[str, RoleConfig]:
    """Build the role configs. Raise ConfigError on any unsafe or missing value."""
    allow_insecure = env.get("OTA_ALLOW_INSECURE_MODEL_ENDPOINT") == "1"
    default_url = env.get("OTA_MODEL_BASE_URL", "").rstrip("/")
    default_key = env.get("OTA_MODEL_KEY", "")
    roles = {}
    for role in ROLES:
        p = f"OTA_{role.upper()}_"
        model = env.get(p + "MODEL", "")
        if not model:
            continue  # role not used
        own_url = env.get(p + "BASE_URL", "").rstrip("/")
        if own_url:
            url, key = own_url, env.get(p + "KEY", "")
            if not key:
                raise ConfigError(f"{role}: own base URL needs its own key")
        else:
            url, key = default_url, env.get(p + "KEY", "") or default_key
        if not url:
            raise ConfigError(f"{role}: no base URL")
        _check_url(role, url, allow_insecure)
        roles[role] = RoleConfig(
            role=role,
            base_url=url,
            key=key,
            model=model,
            rate=float(env.get(p + "RATE", "1")),
            burst=int(env.get(p + "BURST", "1")),
        )
    return roles


def make_buckets(roles: Mapping[str, RoleConfig]) -> dict[str, TokenBucket]:
    """One bucket per endpoint and key. Moderation always gets its own reserved bucket."""
    shared: dict[tuple[str, str], TokenBucket] = {}
    buckets = {}
    for role, cfg in roles.items():
        if role == "moderation":
            buckets[role] = TokenBucket(cfg.rate, cfg.burst)
            continue
        ident = (cfg.base_url, cfg.key)
        if ident not in shared:
            shared[ident] = TokenBucket(cfg.rate, cfg.burst)
        buckets[role] = shared[ident]
    return buckets


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return float(resp.headers["retry-after"])
    except (KeyError, ValueError):
        return None


class ModelClient:
    """Calls for one turn go through `budget`. Logs never contain keys or error bodies."""

    def __init__(
        self,
        roles: Mapping[str, RoleConfig],
        http: httpx.AsyncClient,
        buckets: Mapping[str, TokenBucket] | None = None,
    ) -> None:
        self.roles = roles
        self.http = http
        self.buckets = buckets if buckets is not None else make_buckets(roles)

    def _timeout(self, budget: TurnBudget) -> httpx.Timeout:
        left = max(budget.remaining(), 0.01)
        return httpx.Timeout(left, connect=min(CONNECT_TIMEOUT, left))

    def _headers(self, cfg: RoleConfig) -> dict[str, str]:
        return {"Authorization": f"Bearer {cfg.key}"} if cfg.key else {}

    def _log_429(self, cfg: RoleConfig, attempt: int) -> None:
        log.warning("429 role=%s retry=%d host=%s", cfg.role, attempt, cfg.host)

    def _check(self, cfg: RoleConfig, resp: httpx.Response) -> None:
        if resp.status_code == 429:
            raise Retryable429(_retry_after(resp))
        if resp.status_code >= 400:
            log.warning("error role=%s status=%d host=%s", cfg.role, resp.status_code, cfg.host)
            raise ProviderError(f"{cfg.role}: HTTP {resp.status_code}")

    async def _post(self, role: str, path: str, budget: TurnBudget, **kwargs) -> httpx.Response:
        cfg = self.roles[role]
        budget.count_call(role)

        async def call() -> httpx.Response:
            resp = await self.http.post(
                cfg.base_url + path,
                headers=self._headers(cfg),
                timeout=self._timeout(budget),
                **kwargs,
            )
            self._check(cfg, resp)
            return resp

        return await limited_call(call, self.buckets[role], budget, lambda a: self._log_429(cfg, a))

    async def transcribe(self, wav: bytes, language: str, budget: TurnBudget) -> str:
        cfg = self.roles["stt"]
        resp = await self._post(
            "stt",
            "/audio/transcriptions",
            budget,
            files={"file": ("turn.wav", wav, "audio/wav")},
            data={"model": cfg.model, "language": language},
        )
        return resp.json()["text"]

    async def speak(self, text: str, voice: str, budget: TurnBudget) -> bytes:
        cfg = self.roles["tts"]
        resp = await self._post(
            "tts",
            "/audio/speech",
            budget,
            json={"model": cfg.model, "input": text, "voice": voice, "response_format": "wav"},
        )
        return resp.content

    async def chat(self, messages: list[dict], budget: TurnBudget) -> AsyncIterator[str]:
        """Stream text deltas. A 429 is retried only before the first byte arrives."""
        cfg = self.roles["chat"]
        budget.count_call("chat")
        body = {"model": cfg.model, "messages": messages, "stream": True}

        async def open_stream() -> httpx.Response:
            req = self.http.build_request(
                "POST",
                cfg.base_url + "/chat/completions",
                headers=self._headers(cfg),
                json=body,
                timeout=self._timeout(budget),
            )
            resp = await self.http.send(req, stream=True)
            if resp.status_code >= 400:
                await resp.aclose()
            self._check(cfg, resp)
            return resp

        resp = await limited_call(
            open_stream, self.buckets["chat"], budget, lambda a: self._log_429(cfg, a)
        )
        try:
            async for line in resp.aiter_lines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                delta = json.loads(line[6:])["choices"][0]["delta"].get("content")
                if delta:
                    yield delta
        finally:
            await resp.aclose()

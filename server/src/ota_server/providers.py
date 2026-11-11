"""Thin clients for OpenAI-compatible model endpoints, one per role (ADR 0002).

Every call of a turn goes through its TurnBudget: the token bucket, the 429 retry rules,
the per-turn call caps and a hard deadline over the whole call, body included.
Logs carry role, status, retry count and host only: never keys, URLs or error bodies.
Any failure that is not a 429 or the deadline becomes ProviderError with the role.
"""

import asyncio
import json
import logging
import math
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping

import httpx

from .endpoints import CHAT, EMBEDDINGS, MODERATION, STT, TTS, RoleConfig, bucket_id
from .ratelimit import DeadlineExceeded, Retryable429, TokenBucket, TurnBudget, limited_call

log = logging.getLogger(__name__)

CONNECT_TIMEOUT = 3.0
CALL_CAPS = {CHAT: 4, STT: 2, TTS: 20, MODERATION: 40, EMBEDDINGS: 4}  # per turn
MAX_BODY = 8 * 1024 * 1024  # bytes, any response
MAX_UPLOAD = 4 * 1024 * 1024  # bytes, STT input
MAX_TTS_TEXT = 1000  # characters per TTS call
MAX_SSE_LINE = 64 * 1024  # bytes
MAX_CHAT_TEXT = 16 * 1024  # characters per chat reply
SSE_PREFIX = "data: "
SSE_DONE = "[DONE]"
WAV = ("turn.wav", "audio/wav", "wav")  # file name, MIME type, response_format


class ProviderError(Exception):
    """A model call failed with an error that is not a 429 or the deadline."""

    def __init__(self, role: str, status: int | None = None) -> None:
        super().__init__(f"{role}: " + (f"HTTP {status}" if status else "call failed"))
        self.role = role
        self.status = status


def new_turn_budget() -> TurnBudget:
    return TurnBudget(max_calls=CALL_CAPS)


def make_http_client() -> httpx.AsyncClient:
    """The shared client. No redirects and no proxy settings from the environment,
    so a key never goes to a host other than the base URL of its role."""
    return httpx.AsyncClient(follow_redirects=False, trust_env=False)


def make_buckets(roles: Mapping[str, RoleConfig]) -> dict[str, TokenBucket]:
    shared: dict[tuple[str, str], TokenBucket] = {}
    for cfg in roles.values():
        shared.setdefault(bucket_id(cfg), TokenBucket(cfg.rate, cfg.burst))
    return {role: shared[bucket_id(cfg)] for role, cfg in roles.items()}


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        value = float(resp.headers["retry-after"])
    except (KeyError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


class ModelClient:
    def __init__(
        self,
        roles: Mapping[str, RoleConfig],
        http: httpx.AsyncClient,
        buckets: Mapping[str, TokenBucket] | None = None,
    ) -> None:
        if http.follow_redirects:
            raise ValueError("the HTTP client must not follow redirects")
        self.roles = roles
        self.http = http
        self.buckets = buckets if buckets is not None else make_buckets(roles)

    def _check(self, cfg: RoleConfig, status: int) -> None:
        """Any 3xx, 4xx or 5xx other than 429 is a failure. Redirects are never followed."""
        if status >= 300:
            log.warning("error role=%s status=%d host=%s", cfg.role, status, cfg.host)
            raise ProviderError(cfg.role, status)

    async def _limited[T](
        self, role: str, budget: TurnBudget, call: Callable[[RoleConfig], Awaitable[T]]
    ) -> T:
        """Count the call, then run it under the bucket, the retry rules and the deadline."""
        cfg = self.roles[role]
        budget.count_call(role)

        def log_429(attempt: int) -> None:
            log.warning("429 role=%s status=429 retry=%d host=%s", role, attempt, cfg.host)

        try:
            async with asyncio.timeout(max(budget.remaining(), 0)):
                return await limited_call(lambda: call(cfg), self.buckets[role], budget, log_429)
        except TimeoutError:
            raise DeadlineExceeded() from None
        except httpx.HTTPError:
            log.warning("error role=%s status=none host=%s", role, cfg.host)
            raise ProviderError(role) from None

    def _request(self, cfg: RoleConfig, path: str, budget: TurnBudget, **kw) -> httpx.Request:
        left = max(budget.remaining(), 0.01)
        return self.http.build_request(
            "POST",
            cfg.base_url + path,
            headers={"Authorization": f"Bearer {cfg.key}"} if cfg.key else {},
            timeout=httpx.Timeout(left, connect=min(CONNECT_TIMEOUT, left)),
            **kw,
        )

    async def _open(self, cfg: RoleConfig, req: httpx.Request) -> httpx.Response:
        """Send and check the status. The caller must close the response."""
        resp = await self.http.send(req, stream=True)
        try:
            if resp.status_code == 429:
                raise Retryable429(_retry_after(resp))
            self._check(cfg, resp.status_code)
            size = resp.headers.get("content-length", "")
            if size.isdigit() and int(size) > MAX_BODY:
                raise ProviderError(cfg.role)
        except BaseException:
            await resp.aclose()
            raise
        return resp

    async def _post(self, role: str, path: str, budget: TurnBudget, **kw) -> bytes:
        async def call(cfg: RoleConfig) -> bytes:
            resp = await self._open(cfg, self._request(cfg, path, budget, **kw))
            body = bytearray()
            try:
                async for chunk in resp.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_BODY:
                        raise ProviderError(role)
            finally:
                await resp.aclose()
            return bytes(body)

        return await self._limited(role, budget, call)

    async def transcribe(self, wav: bytes, language: str, budget: TurnBudget) -> str:
        if len(wav) > MAX_UPLOAD:
            raise ProviderError(STT)
        name, mime, _ = WAV
        body = await self._post(
            STT,
            "/audio/transcriptions",
            budget,
            files={"file": (name, wav, mime)},
            data={"model": self.roles[STT].model, "language": language},
        )
        try:
            return json.loads(body)["text"]
        except (ValueError, KeyError, TypeError):
            raise ProviderError(STT) from None

    async def speak(self, text: str, voice: str, budget: TurnBudget) -> bytes:
        if len(text) > MAX_TTS_TEXT:
            raise ProviderError(TTS)
        body = {"model": self.roles[TTS].model, "input": text, "voice": voice}
        body["response_format"] = WAV[2]
        return await self._post(TTS, "/audio/speech", budget, json=body)

    async def chat(self, messages: list[dict], budget: TurnBudget) -> AsyncIterator[str]:
        """Stream text deltas. A 429 is retried only before the first byte arrives.

        Callers must close the stream: `async with contextlib.aclosing(client.chat(...))`.
        """
        body = {"model": self.roles[CHAT].model, "messages": messages, "stream": True}

        async def call(cfg: RoleConfig) -> httpx.Response:
            return await self._open(cfg, self._request(cfg, "/chat/completions", budget, json=body))

        resp = await self._limited(CHAT, budget, call)
        lines = resp.aiter_lines()
        total = 0
        try:
            while True:
                try:
                    line = await asyncio.wait_for(anext(lines), max(budget.remaining(), 0))
                except StopAsyncIteration:
                    return
                except TimeoutError:
                    raise DeadlineExceeded() from None
                if len(line) > MAX_SSE_LINE:
                    raise ProviderError(CHAT)
                delta = _sse_delta(line)
                if delta is None:
                    continue
                total += len(delta)
                if total > MAX_CHAT_TEXT:
                    raise ProviderError(CHAT)
                yield delta
        except httpx.HTTPError:
            raise ProviderError(CHAT) from None
        finally:
            await resp.aclose()


def _sse_delta(line: str) -> str | None:
    """Text of one SSE chunk, or None for comments, empty chunks and the end marker."""
    if not line.startswith(SSE_PREFIX):
        return None
    data = line.removeprefix(SSE_PREFIX)
    if data == SSE_DONE:
        return None
    try:
        choices = json.loads(data).get("choices") or []
        return (choices[0].get("delta") or {}).get("content") or None if choices else None
    except (ValueError, AttributeError, TypeError):
        raise ProviderError(CHAT) from None

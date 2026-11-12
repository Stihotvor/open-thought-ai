"""A fake OpenAI-compatible endpoint for tests, built on httpx.MockTransport."""

import inspect

import httpx

from ota_server.endpoints import load_roles
from ota_server.providers import ModelClient


def as_network(resp: httpx.Response) -> httpx.Response:
    """MockTransport hands back a response that is already read. A real one is not, and
    the client reads raw bytes, so rebuild it with an unread stream."""
    if not isinstance(resp.stream, httpx.ByteStream):
        return resp  # a custom test stream, already unread
    return httpx.Response(
        resp.status_code, headers=resp.headers, stream=httpx.ByteStream(resp.content)
    )


def make_client(handler, env, trust_env=False, **http_kw) -> ModelClient:
    """A ModelClient whose HTTP calls go to `handler(request) -> Response` (sync or async)."""

    async def network(req):
        resp = handler(req)
        if inspect.isawaitable(resp):
            resp = await resp
        return as_network(resp)

    http = httpx.AsyncClient(transport=httpx.MockTransport(network), trust_env=trust_env, **http_kw)
    return ModelClient(load_roles(env), http)

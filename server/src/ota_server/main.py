"""FastAPI app factory. Run: uvicorn ota_server.main:app"""

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from .config import Settings, get_settings, model_env
from .endpoints import voice_roles
from .gateway import router
from .mcp.config import load_servers
from .mcp.registry import McpRegistry
from .mcp.source import McpSource
from .pipeline import VoicePipeline
from .providers import ModelClient, make_http_client
from .reply import ReplySource
from .session import echo

log = logging.getLogger(__name__)


async def start_reply_source(settings: Settings, stack: AsyncExitStack) -> ReplySource:
    """Echo, or the voice pipeline with its HTTP client. The stack closes the client."""
    if settings.pipeline != "voice":
        return echo
    roles = voice_roles(model_env())
    http = await stack.enter_async_context(make_http_client())
    return VoicePipeline(ModelClient(roles, http), settings.language, settings.tts_voice)


async def start_mcp_sources(settings: Settings, stack: AsyncExitStack) -> list[McpSource]:
    """The external MCP servers, shared by all sessions. The stack closes them (D10)."""
    configs = load_servers(settings.mcp_servers_file)
    return await McpRegistry.start(configs, stack)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()  # fail at startup on missing or invalid settings
    async with AsyncExitStack() as stack:
        application.state.reply_source = await start_reply_source(settings, stack)
        application.state.mcp_sources = await start_mcp_sources(settings, stack)
        yield


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    application = FastAPI(title="open-thought-ai server", lifespan=lifespan)
    application.include_router(router)

    @application.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()

"""FastAPI app factory. Run: uvicorn ota_server.main:app"""

import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from .config import Settings, get_settings, model_env
from .endpoints import CHAT, MODERATION, STT, TTS, ConfigError, RoleConfig, load_roles
from .gateway import router
from .pipeline import VoicePipeline
from .providers import ModelClient, make_http_client
from .session import ReplySource, echo

log = logging.getLogger(__name__)

VOICE_ROLES = (STT, CHAT, TTS)


def voice_roles(env: Mapping[str, str], settings: Settings) -> dict[str, RoleConfig]:
    """The model roles for the voice pipeline. Raises ConfigError when it must not start."""
    if not settings.voice_without_moderation:
        raise ConfigError(
            "the voice pipeline has no moderation yet: set OTA_VOICE_WITHOUT_MODERATION=1 "
            "to accept that (not for use with children)"
        )
    roles = load_roles(env)
    missing = [role for role in VOICE_ROLES if role not in roles]
    if missing:
        raise ConfigError(f"the voice pipeline needs these roles: {', '.join(missing)}")
    log.warning("voice pipeline runs WITHOUT moderation: not for use with children")
    if MODERATION in roles:
        log.warning("the moderation role is configured but not used yet")
    return roles


async def start_reply_source(settings: Settings, stack: AsyncExitStack) -> ReplySource:
    """Echo, or the voice pipeline with its HTTP client. The stack closes the client."""
    if settings.pipeline != "voice":
        return echo
    roles = voice_roles(model_env(), settings)
    http = await stack.enter_async_context(make_http_client())
    return VoicePipeline(ModelClient(roles, http), settings.language, settings.tts_voice)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()  # fail at startup on missing or invalid settings
    async with AsyncExitStack() as stack:
        application.state.reply_source = await start_reply_source(settings, stack)
        yield


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    application = FastAPI(title="open-thought-ai server", lifespan=lifespan)
    application.include_router(router)

    @application.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    return application


app = create_app()

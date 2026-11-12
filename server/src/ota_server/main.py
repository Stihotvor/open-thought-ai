"""FastAPI app factory. Run: uvicorn ota_server.main:app"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import Settings, get_settings, model_env
from .endpoints import CHAT, STT, TTS, ConfigError, load_roles
from .gateway import router
from .pipeline import VoicePipeline
from .providers import ModelClient, make_http_client
from .session import echo

VOICE_ROLES = (STT, CHAT, TTS)


def check_voice_roles(env: dict[str, str]) -> None:
    """Fail at startup when the voice pipeline cannot run. Raises ConfigError."""
    roles = load_roles(env)
    missing = [role for role in VOICE_ROLES if role not in roles]
    if missing:
        raise ConfigError(f"the voice pipeline needs these roles: {', '.join(missing)}")


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings: Settings = get_settings()  # fail at startup on missing or invalid settings
    application.state.reply_source = echo
    if settings.pipeline != "voice":
        yield
        return
    env = model_env()
    check_voice_roles(env)
    async with make_http_client() as http:
        client = ModelClient(load_roles(env), http)
        application.state.reply_source = VoicePipeline(
            client, settings.language, settings.tts_voice
        )
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

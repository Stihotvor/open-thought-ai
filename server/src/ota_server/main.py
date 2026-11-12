"""FastAPI app factory. Run: uvicorn ota_server.main:app"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import get_settings
from .gateway import router


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_settings()  # fail at startup on missing or invalid settings
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

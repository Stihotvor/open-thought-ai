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
    app = FastAPI(title="open-thought-ai server", lifespan=lifespan)
    app.include_router(router)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    return app


app = create_app()

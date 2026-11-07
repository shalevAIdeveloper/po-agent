from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.routes import health
from app.config import AppEnv, Settings, get_settings
from app.db import dispose_engine
from app.logging import configure_logging
from app.middleware import request_context


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    is_prod = settings.app_env is AppEnv.PROD
    app = FastAPI(
        title="PO Agent API",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if is_prod else "/openapi.json",
    )
    app.state.settings = settings
    app.middleware("http")(request_context)
    app.include_router(health.router)
    return app


app = create_app()

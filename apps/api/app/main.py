from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.routes import health, me, orgs
from app.auth.jwt import JwksClient, KeySource
from app.config import AppEnv, Settings, get_settings
from app.db import DatabaseBusyError, dispose_engine
from app.logging import configure_logging
from app.middleware import REQUEST_ID_HEADER, request_context
from app.services.orgs import OrgServiceError


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()


async def handle_org_service_error(_request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, OrgServiceError):
        raise error
    return JSONResponse(status_code=error.status_code, content={"detail": error.code})


BUSY_RETRY_AFTER_SECONDS = 2


async def handle_database_busy(_request: Request, _error: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"detail": "busy"},
        headers={"Retry-After": str(BUSY_RETRY_AFTER_SECONDS)},
    )


def create_app(settings: Settings | None = None, *, jwks: KeySource | None = None) -> FastAPI:
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
    # Tests inject a static key source; otherwise keys come from the project's JWKS.
    app.state.jwks = jwks or (JwksClient(settings.jwks_url) if settings.jwks_url else None)

    app.middleware("http")(request_context)
    if settings.web_origin:
        # Added last, so it is the outermost middleware and also covers error responses.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[settings.web_origin],
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-Org-Id", REQUEST_ID_HEADER],
            expose_headers=[REQUEST_ID_HEADER],
        )

    app.add_exception_handler(OrgServiceError, handle_org_service_error)
    app.add_exception_handler(DatabaseBusyError, handle_database_busy)
    app.include_router(health.router)
    app.include_router(me.router)
    app.include_router(orgs.router)
    return app


app = create_app()

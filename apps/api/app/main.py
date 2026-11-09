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

    # Interactive docs and the schema only locally (L5); the web's types come from the
    # committed openapi.json, never from a deployed API.
    is_local = settings.app_env is AppEnv.LOCAL
    app = FastAPI(
        title="PO Agent API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if is_local else None,
        redoc_url=None,
        openapi_url="/openapi.json" if is_local else None,
        # Behind Railway's proxy the app sees http; a trailing-slash redirect would point
        # at http:// and a script client could resend its bearer token in clear (L4, 0.5c
        # `sec`). Wrong paths get a plain 404 instead.
        redirect_slashes=False,
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
            # Retry-After: the web backs off on 503 `busy` (A8).
            expose_headers=[REQUEST_ID_HEADER, "Retry-After"],
        )

    app.add_exception_handler(OrgServiceError, handle_org_service_error)
    app.add_exception_handler(DatabaseBusyError, handle_database_busy)
    app.include_router(health.router)
    app.include_router(me.router)
    app.include_router(orgs.router)
    return app


app = create_app()

# syntax=docker/dockerfile:1.27@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# One image for the API and the worker; the worker's start command is set in
# .railway/railway.ts. Build context: apps/api.
# Base images are pinned by digest (tag kept for readability): a re-pushed tag must
# not change what runs next to DATABASE_URL.

FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

FROM python:3.12.16-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
# Runtime dependencies only: no dev group (no fastapi[standard] / OpenTelemetry SDK, L6).
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app

FROM python:3.12.16-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS runtime
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OTEL_SDK_DISABLED=true \
    APP_ENV=prod \
    PO_DEPLOYED=1
# APP_ENV=prod is the fail-closed default: an image started without APP_ENV gets the
# strictest settings (every cloud setting required, https only, no docs, no .env files).
# Railway dev sets APP_ENV=dev explicitly. PO_DEPLOYED=1 makes APP_ENV=local (or an
# empty APP_ENV, which falls back to local) a startup error inside this image.
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY --from=build /app/app ./app
USER app
EXPOSE 8000
# No access log (it would include query strings; request_context logs each request
# without them) and no `server` header. Proxy headers stay at uvicorn's default
# (trusted from 127.0.0.1 only): the API does not use the client IP (see ADR 0008).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-8000}\" --no-access-log --no-server-header"]

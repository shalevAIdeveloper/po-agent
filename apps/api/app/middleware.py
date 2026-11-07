import re
from collections.abc import Awaitable, Callable
from time import perf_counter
from uuid import uuid4

import structlog
from fastapi import Request, Response

REQUEST_ID_HEADER = "x-request-id"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")

log = structlog.get_logger(__name__)


def _request_id(request: Request) -> str:
    incoming = request.headers.get(REQUEST_ID_HEADER, "")
    # Only accept well-formed ids from callers; anything else could inject into logs.
    return incoming if _VALID_REQUEST_ID.fullmatch(incoming) else uuid4().hex


async def request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Binds a request id to every log line and logs one summary line per request.

    Never logs headers, query strings or bodies.
    """
    request_id = _request_id(request)
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    started = perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("http.request_failed", method=request.method, path=request.url.path)
        raise
    log.info(
        "http.request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=round((perf_counter() - started) * 1000, 1),
    )
    response.headers[REQUEST_ID_HEADER] = request_id
    return response

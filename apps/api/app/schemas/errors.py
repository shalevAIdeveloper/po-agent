"""Error bodies, documented in OpenAPI so the web client gets typed errors (A5)."""

from typing import Any

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """Every handled error: `detail` is a fixed message or a stable code, never data."""

    detail: str


def error_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """`responses=` for a route: the listed statuses all return ErrorResponse."""
    return {status: {"model": ErrorResponse} for status in statuses}


# Every authenticated route: 401 bad/missing token, 503 auth unavailable or DB busy.
AUTH_ERRORS = (401, 503)
# Org-scoped routes (X-Org-Id): 400 selection required, 404 not a member.
ORG_ERRORS = (*AUTH_ERRORS, 400, 404)

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

_ASYNC_DRIVER = "postgresql+asyncpg://"
_PLAIN_SCHEMES = ("postgres://", "postgresql://")

# The only verified claims passed to Postgres as `request.jwt.claims`. RLS may trust
# sub/role/app_metadata; `user_metadata` is editable by the user and never sent (ADR 0003).
RLS_CLAIMS = frozenset(
    {
        "sub",
        "role",
        "aud",
        "iss",
        "iat",
        "exp",
        "aal",
        "amr",
        "session_id",
        "is_anonymous",
        "app_metadata",
    }
)

# serialization_failure, deadlock_detected: safe to retry the whole transaction.
RETRYABLE_SQLSTATES = frozenset({"40001", "40P01"})
MAX_TRANSACTION_ATTEMPTS = 3
# query_canceled (statement_timeout), lock_not_available (lock_timeout): the DB is
# busy; the client may retry later. Not retried here, so a request never waits long.
STATEMENT_TIMEOUT_SQLSTATE = "57014"
BUSY_SQLSTATES = frozenset({STATEMENT_TIMEOUT_SQLSTATE, "55P03"})
# Waiting for a lock (row, advisory) fails fast instead of eating the 8 s statement budget.
LOCK_TIMEOUT = "3s"

log = structlog.get_logger(__name__)


class DatabaseBusyError(Exception):
    """A statement or lock wait timed out. Mapped to 503 `busy` with Retry-After."""


def to_async_url(url: str) -> str:
    """Supabase hands out postgres(ql):// URLs; SQLAlchemy needs the asyncpg driver."""
    if url.startswith(_ASYNC_DRIVER):
        return url
    for scheme in _PLAIN_SCHEMES:
        if url.startswith(scheme):
            return _ASYNC_DRIVER + url.removeprefix(scheme)
    raise ValueError("DATABASE_URL must start with postgres:// or postgresql://")


@lru_cache
def get_engine() -> AsyncEngine:
    """Created on first use so /health and tests never need a database."""
    database_url = get_settings().database_url
    if database_url is None:
        raise RuntimeError("DATABASE_URL is not configured")
    return create_async_engine(
        to_async_url(database_url.get_secret_value()),
        pool_pre_ping=True,
        # Keep bound values (PO lines, prices, addresses) out of exception messages and logs.
        hide_parameters=True,
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request."""
    async with get_sessionmaker()() as session:
        yield session


def sqlstate(error: BaseException) -> str | None:
    """Postgres SQLSTATE of a DBAPIError raised through SQLAlchemy + asyncpg."""
    orig = getattr(error, "orig", None)
    code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return code if isinstance(code, str) else None


def rls_claims(claims: Mapping[str, Any]) -> dict[str, Any]:
    return {name: value for name, value in claims.items() if name in RLS_CLAIMS}


@asynccontextmanager
async def rls_transaction(claims: Mapping[str, Any]) -> AsyncIterator[AsyncSession]:
    """One READ COMMITTED transaction running as `authenticated` with the caller's claims.

    `claims` must come from a verified token. The API connects as `po_api`, which has
    no privileges of its own, so nothing here can bypass RLS (ADR 0003, ADR 0004).

    Never await external I/O (LLM, storage, HTTP) inside this transaction: po_api's
    `idle_in_transaction_session_timeout` (15 s) would kill the connection.
    """
    async with get_sessionmaker()() as session, session.begin():
        await session.execute(
            text(
                "select set_config('role', 'authenticated', true),"
                " set_config('request.jwt.claims', :claims, true),"
                " set_config('lock_timeout', :lock_timeout, true)"
            ),
            {"claims": json.dumps(rls_claims(claims), default=str), "lock_timeout": LOCK_TIMEOUT},
        )
        yield session


async def run_as_user[T](
    claims: Mapping[str, Any], work: Callable[[AsyncSession], Awaitable[T]]
) -> T:
    """Runs `work` in an RLS transaction, retrying on serialization failures and deadlocks.

    Timeouts (statement or lock) raise DatabaseBusyError instead of a bare 500.
    """
    for attempt in range(1, MAX_TRANSACTION_ATTEMPTS + 1):
        try:
            async with rls_transaction(claims) as session:
                return await work(session)
        except DBAPIError as error:
            code = sqlstate(error)
            if code in BUSY_SQLSTATES:
                if code == STATEMENT_TIMEOUT_SQLSTATE:
                    # A slow query is a bug to fix, not load: alert on it (A8).
                    log.error("db.statement_timeout", sqlstate=code)
                else:
                    log.warning("db.lock_timeout", sqlstate=code)
                raise DatabaseBusyError from None
            if code not in RETRYABLE_SQLSTATES or attempt == MAX_TRANSACTION_ATTEMPTS:
                raise
            log.info("db.transaction_retry", sqlstate=code, attempt=attempt)
    raise AssertionError("unreachable")


async def dispose_engine() -> None:
    if get_engine.cache_info().currsize:
        await get_engine().dispose()
        get_sessionmaker.cache_clear()
        get_engine.cache_clear()

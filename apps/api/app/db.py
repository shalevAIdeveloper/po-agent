from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

_ASYNC_DRIVER = "postgresql+asyncpg://"
_PLAIN_SCHEMES = ("postgres://", "postgresql://")


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


async def dispose_engine() -> None:
    if get_engine.cache_info().currsize:
        await get_engine().dispose()
        get_sessionmaker.cache_clear()
        get_engine.cache_clear()

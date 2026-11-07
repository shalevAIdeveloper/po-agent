import os

import pytest
from pydantic import SecretStr
from sqlalchemy import text

from app import db
from app.config import Settings


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://u:p@h:5432/d", "postgresql+asyncpg://u:p@h:5432/d"),
        ("postgres://u:p@h:5432/d", "postgresql+asyncpg://u:p@h:5432/d"),
        ("postgresql+asyncpg://u:p@h:5432/d", "postgresql+asyncpg://u:p@h:5432/d"),
    ],
)
def test_to_async_url(url: str, expected: str) -> None:
    assert db.to_async_url(url) == expected


def test_to_async_url_rejects_other_schemes() -> None:
    with pytest.raises(ValueError, match="DATABASE_URL"):
        db.to_async_url("mysql://u:p@h/d")


def test_get_engine_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "get_settings", lambda: Settings(_env_file=None))
    db.get_engine.cache_clear()

    with pytest.raises(RuntimeError, match="DATABASE_URL is not configured"):
        db.get_engine()


@pytest.mark.integration
async def test_select_one_against_local_db(monkeypatch: pytest.MonkeyPatch) -> None:
    url = os.environ.get("INTEGRATION_DATABASE_URL")
    if not url:
        pytest.skip("INTEGRATION_DATABASE_URL not set (start local Supabase first)")
    monkeypatch.setattr(
        db, "get_settings", lambda: Settings(_env_file=None, database_url=SecretStr(url))
    )
    db.get_engine.cache_clear()
    try:
        async for session in db.get_session():
            assert (await session.execute(text("select 1"))).scalar_one() == 1
    finally:
        await db.dispose_engine()

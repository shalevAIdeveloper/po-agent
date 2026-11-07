import io
import os

import pytest
import structlog
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import db
from app.config import Settings
from app.logging import configure_logging


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


def test_engine_hides_bound_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    url = SecretStr("postgresql://u:p@127.0.0.1:1/d")
    monkeypatch.setattr(db, "get_settings", lambda: Settings(_env_file=None, database_url=url))
    db.get_engine.cache_clear()
    try:
        # No connection is opened; this only inspects the engine configuration.
        assert db.get_engine().sync_engine.hide_parameters is True
    finally:
        db.get_engine.cache_clear()


def test_db_error_in_log_has_no_parameter_values() -> None:
    stream = io.StringIO()
    configure_logging(stream=stream)
    # Worst case: an error built WITHOUT hide_parameters, so its message embeds the values.
    error = DBAPIError.instance(
        "insert into order_lines (description) values ($1)",
        ("ACME widget 123 Main St $4.50",),
        Exception("value too long"),
        Exception,
        hide_parameters=False,
    )
    assert "ACME widget" in str(error)

    try:
        raise error
    except DBAPIError:
        structlog.get_logger().exception("db.insert_failed")

    output = stream.getvalue()
    assert "ACME widget" not in output
    assert "sqlalchemy.exc.DBAPIError" in output


@pytest.mark.integration
async def test_db_error_message_has_no_parameter_values(monkeypatch: pytest.MonkeyPatch) -> None:
    url = os.environ.get("INTEGRATION_DATABASE_URL")
    if not url:
        pytest.skip("INTEGRATION_DATABASE_URL not set (start local Supabase first)")
    monkeypatch.setattr(
        db, "get_settings", lambda: Settings(_env_file=None, database_url=SecretStr(url))
    )
    db.get_engine.cache_clear()
    stream = io.StringIO()
    configure_logging(stream=stream)
    try:
        async for session in db.get_session():
            with pytest.raises(DBAPIError) as excinfo:
                await session.execute(text("select cast(:v as int)"), {"v": "ACME-SECRET-VALUE"})
            # SQLAlchemy drops its [parameters: ...] section. The driver's own message may
            # still quote the value, so DB error text is never safe outside sanitized logs.
            assert "SQL parameters hidden due to hide_parameters=True" in str(excinfo.value)
            try:
                raise excinfo.value
            except DBAPIError:
                structlog.get_logger().exception("db.query_failed")
        assert "ACME-SECRET-VALUE" not in stream.getvalue()
    finally:
        await db.dispose_engine()


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

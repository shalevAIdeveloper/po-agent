import io
import ssl

import pytest
import structlog
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import db
from app.config import AppEnv, Settings
from app.logging import configure_logging
from tests.ca import make_ca_pem
from tests.integration_support import require_env


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


def _cloud_settings(url: str) -> Settings:
    return Settings(
        _env_file=None,
        app_env=AppEnv.DEV,
        database_url=SecretStr(url),
        database_ca_cert=make_ca_pem(),
        supabase_url="https://example.supabase.co",
        web_origin="https://app.example.com",
    )


@pytest.mark.parametrize(
    "query", ["", "?sslmode=require", "?sslmode=verify-full&sslrootcert=/x/ca.pem&ssl=true"]
)
def test_cloud_engine_always_verifies_tls(query: str) -> None:
    url, connect_args = db.engine_url_and_connect_args(
        _cloud_settings(f"postgresql://po_api.ref:p@pooler.example:5432/postgres{query}")
    )

    context = connect_args["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is True
    # URL TLS options are dropped: asyncpg would get them as unknown keywords, and they
    # must not weaken the context set above.
    assert dict(url.query) == {}
    assert url.drivername == "postgresql+asyncpg"
    assert url.host == "pooler.example"
    assert url.username == "po_api.ref"


def test_local_engine_keeps_url_and_has_no_tls() -> None:
    raw = "postgresql://po_api:p@127.0.0.1:54322/postgres?application_name=x"
    url, connect_args = db.engine_url_and_connect_args(
        Settings(_env_file=None, database_url=SecretStr(raw))
    )

    assert connect_args == {}
    assert dict(url.query) == {"application_name": "x"}


def test_get_engine_uses_the_tls_context_outside_local(monkeypatch: pytest.MonkeyPatch) -> None:
    # The engine must actually receive the verify-full context, not only compute it.
    settings = _cloud_settings("postgresql://u:p@pooler.example:5432/postgres?sslmode=require")
    calls: list[tuple[object, dict[str, object]]] = []

    def spy(url: object, **kwargs: object) -> object:
        calls.append((url, kwargs))
        return object()

    monkeypatch.setattr(db, "get_settings", lambda: settings)
    monkeypatch.setattr(db, "create_async_engine", spy)
    db.get_engine.cache_clear()
    try:
        db.get_engine()
    finally:
        db.get_engine.cache_clear()

    [(url, kwargs)] = calls
    connect_args = kwargs["connect_args"]
    assert isinstance(connect_args, dict)
    context = connect_args["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert "sslmode" not in str(url)


def test_cloud_engine_keeps_other_query_params() -> None:
    url, _ = db.engine_url_and_connect_args(
        _cloud_settings("postgresql://u:p@h.example/postgres?sslmode=require&application_name=x")
    )

    assert dict(url.query) == {"application_name": "x"}


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


def _integration_database_url() -> str:
    return require_env("INTEGRATION_DATABASE_URL")


@pytest.mark.integration
async def test_db_error_message_has_no_parameter_values(monkeypatch: pytest.MonkeyPatch) -> None:
    url = _integration_database_url()
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
    url = _integration_database_url()
    monkeypatch.setattr(
        db, "get_settings", lambda: Settings(_env_file=None, database_url=SecretStr(url))
    )
    db.get_engine.cache_clear()
    try:
        async for session in db.get_session():
            assert (await session.execute(text("select 1"))).scalar_one() == 1
    finally:
        await db.dispose_engine()

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from app.config import AppEnv, Settings


def test_local_defaults_need_no_env() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_env is AppEnv.LOCAL
    assert settings.database_url is None


@pytest.mark.parametrize("env", ["dev", "prod"])
def test_cloud_env_fails_fast_when_required_settings_missing(
    env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", env)

    with pytest.raises(ValidationError, match="DATABASE_URL, SUPABASE_URL, WEB_ORIGIN"):
        Settings(_env_file=None)


def test_cloud_env_accepts_complete_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.example:5432/postgres")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("WEB_ORIGIN", "https://app.example.com")

    settings = Settings(_env_file=None)

    assert settings.app_env is AppEnv.PROD
    assert settings.jwt_issuer == "https://example.supabase.co/auth/v1"
    assert settings.jwks_url == "https://example.supabase.co/auth/v1/.well-known/jwks.json"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.supabase.co/",
        "example.supabase.co",
        "https://h/auth/v1",
        "https://h.example?x=1",
        "https://h.example#frag",
        "https://user@h.example",
        "https://evil.example\\@h.example",
        "https://h.example.",
        "https://..",
        "https://a..b",
        "https://-h.example",
        "https://h.example:99999",
        "https://h.example:65536",
        "https://h.example:0",
        "https://h.example:00000",
        "HTTPS://h.example",
    ],
)
def test_supabase_url_must_be_a_bare_origin(url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, supabase_url=url)


@pytest.mark.parametrize("field", ["SUPABASE_URL", "WEB_ORIGIN"])
def test_cloud_env_requires_https(monkeypatch: pytest.MonkeyPatch, field: str) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.example:5432/postgres")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("WEB_ORIGIN", "https://app.example.com")
    monkeypatch.setenv(field, "http://plain.example.com")

    with pytest.raises(ValidationError, match=f"https is required outside local for: {field}"):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "url", ["http://localhost:3000", "https://abc-123.supabase.co", "http://127.0.0.1:65535"]
)
def test_valid_origins_are_accepted(url: str) -> None:
    assert Settings(_env_file=None, web_origin=url).web_origin == url


def test_local_allows_plain_http() -> None:
    settings = Settings(_env_file=None, supabase_url="http://127.0.0.1:54321")

    assert settings.jwt_issuer == "http://127.0.0.1:54321/auth/v1"


def test_log_level_is_validated() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, log_level="verbose")  # type: ignore[arg-type]


def test_database_url_is_hidden_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:supersecret@db.example:5432/postgres")

    settings = Settings(_env_file=None)

    assert "supersecret" not in repr(settings)


def test_config_error_does_not_echo_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    # Short URL on purpose: pydantic only truncates long inputs, so this one would be shown.
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("DATABASE_URL", "postgres://u:pw-XYZ@h/d")

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)

    assert "SUPABASE_URL" in str(excinfo.value)
    assert "pw-XYZ" not in str(excinfo.value)


def test_empty_env_var_counts_as_missing_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")

    with pytest.raises(ValidationError, match="DATABASE_URL"):
        Settings(_env_file=None)


def test_empty_value_in_env_file_counts_as_missing_in_prod(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    # Same shape as .env.example: keys present, values empty.
    env_file.write_text("APP_ENV=prod\nDATABASE_URL=\nSUPABASE_URL=\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="DATABASE_URL, SUPABASE_URL"):
        Settings(_env_file=env_file)


def test_whitespace_only_value_counts_as_missing_in_prod() -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        Settings(
            _env_file=None,
            app_env=AppEnv.PROD,
            database_url=SecretStr("   "),
            supabase_url="https://example.supabase.co",
        )


def test_poll_interval_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, worker_poll_interval_seconds=0)

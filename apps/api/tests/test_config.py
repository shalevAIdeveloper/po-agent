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

    with pytest.raises(ValidationError, match="DATABASE_URL, SUPABASE_URL"):
        Settings(_env_file=None)


def test_cloud_env_accepts_complete_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.example:5432/postgres")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")

    settings = Settings(_env_file=None)

    assert settings.app_env is AppEnv.PROD


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

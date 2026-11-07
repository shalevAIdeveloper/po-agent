import pytest
from pydantic import ValidationError

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


def test_poll_interval_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, worker_poll_interval_seconds=0)

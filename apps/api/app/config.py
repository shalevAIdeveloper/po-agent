from enum import StrEnum
from functools import lru_cache
from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    LOCAL = "local"
    DEV = "dev"
    PROD = "prod"


# Settings that may be empty locally but must be set in every cloud environment.
REQUIRED_OUTSIDE_LOCAL: tuple[str, ...] = ("database_url", "supabase_url")


def _is_blank(value: SecretStr | str | None) -> bool:
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    return value is None or not value.strip()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Repo-root .env first, then apps/api/.env; real env vars win over both.
        env_file=("../../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        # `DATABASE_URL=` (as in .env.example) counts as unset, not as an empty value.
        env_ignore_empty=True,
        # Validation errors must never echo setting values (they include secrets).
        hide_input_in_errors=True,
    )

    app_env: AppEnv = AppEnv.LOCAL
    log_level: str = "INFO"

    database_url: SecretStr | None = None
    supabase_url: str | None = None

    worker_poll_interval_seconds: float = Field(default=1.0, gt=0)

    @model_validator(mode="after")
    def _require_cloud_settings(self) -> Self:
        if self.app_env is AppEnv.LOCAL:
            return self
        missing = [name for name in REQUIRED_OUTSIDE_LOCAL if _is_blank(getattr(self, name))]
        if missing:
            raise ValueError(
                f"missing required settings for APP_ENV={self.app_env}: "
                + ", ".join(name.upper() for name in missing)
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

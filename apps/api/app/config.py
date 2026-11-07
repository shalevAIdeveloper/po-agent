from enum import StrEnum
from functools import lru_cache
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    LOCAL = "local"
    DEV = "dev"
    PROD = "prod"


# Settings that may be empty locally but must be set in every cloud environment.
REQUIRED_OUTSIDE_LOCAL: tuple[str, ...] = ("database_url", "supabase_url", "web_origin")

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]

# A bare origin: scheme, host, optional port. No path, query, fragment or userinfo.
# Host labels must start and end with a letter/digit (no "..", no trailing dot).
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
_ORIGIN_PATTERN = rf"^https?://{_LABEL}(?:\.{_LABEL})*(:\d{{1,5}})?$"
_MAX_PORT = 65535

# Outside local these must use TLS: the JWKS fetched over plain http could be
# swapped in transit, letting anyone on the path mint valid tokens.
HTTPS_OUTSIDE_LOCAL: tuple[str, ...] = ("supabase_url", "web_origin")


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
    log_level: LogLevel = "INFO"

    # The API connects as `po_api` (ADR 0003); the worker as a bypass role.
    database_url: SecretStr | None = None
    # e.g. http://127.0.0.1:54321; JWT issuer and JWKS are derived from it.
    supabase_url: str | None = Field(default=None, pattern=_ORIGIN_PATTERN)
    # The only browser origin allowed by CORS, e.g. http://localhost:3000.
    web_origin: str | None = Field(default=None, pattern=_ORIGIN_PATTERN)

    worker_poll_interval_seconds: float = Field(default=1.0, gt=0)

    @property
    def jwt_issuer(self) -> str | None:
        return f"{self.supabase_url}/auth/v1" if self.supabase_url else None

    @property
    def jwks_url(self) -> str | None:
        return f"{self.jwt_issuer}/.well-known/jwks.json" if self.jwt_issuer else None

    @field_validator("supabase_url", "web_origin")
    @classmethod
    def _port_in_range(cls, value: str | None) -> str | None:
        if value is not None and (port := urlsplit(value).port) is not None and port > _MAX_PORT:
            raise ValueError("port out of range")
        return value

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
        insecure = [
            name
            for name in HTTPS_OUTSIDE_LOCAL
            if not str(getattr(self, name)).startswith("https://")
        ]
        if insecure:
            raise ValueError(
                f"https is required outside local for: {', '.join(n.upper() for n in insecure)}"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

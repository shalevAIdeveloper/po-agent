import os
import re
import ssl
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import parse_qsl, urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class AppEnv(StrEnum):
    LOCAL = "local"
    DEV = "dev"
    PROD = "prod"


# Settings that may be empty locally but must be set in every cloud environment.
REQUIRED_OUTSIDE_LOCAL: tuple[str, ...] = (
    "database_url",
    "supabase_url",
    "web_origin",
    "database_ca_cert",
)

# sslmode values that allow an unencrypted connection (libpq semantics).
WEAK_SSLMODES = frozenset({"disable", "allow", "prefer"})
# Query parameters DATABASE_URL may carry outside local, besides TLS options. TLS
# options (ssl*, direct_tls) are dropped by db.py: TLS always comes from DATABASE_CA_CERT.
# Anything else would reach asyncpg.connect() as an unknown keyword and fail at the
# first query, after the deploy already looked healthy.
ALLOWED_DB_URL_PARAMS = frozenset({"application_name"})


def is_tls_url_param(key: str) -> bool:
    lowered = key.lower()
    return lowered.startswith("ssl") or lowered == "direct_tls"


def is_deployed() -> bool:
    """True inside the deploy image (PO_DEPLOYED=1) or on Railway (M1, 0.5c `sec`)."""
    return os.environ.get("PO_DEPLOYED", "").strip() == "1" or bool(
        os.environ.get("RAILWAY_ENVIRONMENT_ID", "").strip()
    )


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
LLMProvider = Literal["anthropic", "fake"]
LLMEffort = Literal["low", "medium", "high", "xhigh", "max"]
# A provider model id, e.g. claude-sonnet-5-5. Only a sanity check: the provider decides.
_MODEL_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,99}$"

# A bare origin: scheme, host, optional port. No path, query, fragment or userinfo.
# Host labels must start and end with a letter/digit (no "..", no trailing dot).
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
_ORIGIN_PATTERN = rf"^https?://{_LABEL}(?:\.{_LABEL})*(:\d{{1,5}})?$"
_MAX_PORT = 65535
_PORT_SUFFIX = re.compile(r":(\d{1,5})$")

# Outside local these must use TLS: the JWKS fetched over plain http could be
# swapped in transit, letting anyone on the path mint valid tokens.
HTTPS_OUTSIDE_LOCAL: tuple[str, ...] = ("supabase_url", "web_origin")


# .env files are found from this file, not from the working directory (L4):
# the repo-root .env first, then apps/api/.env.
_API_DIR = Path(__file__).resolve().parents[1]
ENV_FILES: tuple[Path, ...] = (_API_DIR.parent.parent / ".env", _API_DIR / ".env")


def reads_env_files() -> bool:
    """.env files are a local convenience only (L4).

    In a deployed environment (APP_ENV set to anything but `local` in the real
    environment, or running in the deploy image / on Railway), a .env file that
    slipped into an image must never be read.
    """
    if is_deployed():
        return False
    return os.environ.get("APP_ENV", AppEnv.LOCAL).strip().lower() == AppEnv.LOCAL


def _is_blank(value: SecretStr | str | None) -> bool:
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    return value is None or not value.strip()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Real env vars win over both files; files are skipped outside local (L4).
        env_file=ENV_FILES,
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
    # PEM of the Supabase CA (Dashboard → Database → SSL). Public, not a secret. Outside
    # local the DB connection always uses TLS and verifies chain + hostname against it.
    database_ca_cert: str | None = None
    # e.g. http://127.0.0.1:54321; JWT issuer and JWKS are derived from it.
    supabase_url: str | None = Field(default=None, pattern=_ORIGIN_PATTERN)
    # The only browser origin allowed by CORS, e.g. http://localhost:3000.
    web_origin: str | None = Field(default=None, pattern=_ORIGIN_PATTERN)

    worker_poll_interval_seconds: float = Field(default=1.0, gt=0)

    # LLM (ADR 0011). The key and models are required only where an LLM client is built
    # (worker, evals --live): app.llm.factory checks them, so the API process never needs
    # the key. `fake` is refused outside local and in a deployed image.
    llm_provider: LLMProvider = "anthropic"
    llm_api_key: SecretStr | None = None
    llm_extraction_model: str | None = Field(default=None, pattern=_MODEL_ID_PATTERN)
    llm_mapping_model: str | None = Field(default=None, pattern=_MODEL_ID_PATTERN)
    # Per attempt; the adapter also bounds the whole call including transport retries.
    llm_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    # Non-streaming requests: kept well under what a single HTTP response can carry.
    llm_max_output_tokens: int = Field(default=16000, ge=1, le=32000)
    llm_effort: LLMEffort = "medium"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        if reads_env_files():
            return init_settings, env_settings, dotenv_settings, file_secret_settings
        return init_settings, env_settings, file_secret_settings

    @property
    def jwt_issuer(self) -> str | None:
        return f"{self.supabase_url}/auth/v1" if self.supabase_url else None

    @property
    def jwks_url(self) -> str | None:
        return f"{self.jwt_issuer}/.well-known/jwks.json" if self.jwt_issuer else None

    @field_validator("supabase_url", "web_origin")
    @classmethod
    def _port_in_range(cls, value: str | None) -> str | None:
        # The pattern already limits the port to 1-5 digits; read it from the string
        # rather than urlsplit(), whose `.port` raises on out-of-range values (A8).
        if value is not None and (match := _PORT_SUFFIX.search(value)):
            port = int(match.group(1))
            if not 1 <= port <= _MAX_PORT:
                raise ValueError("port out of range")
        return value

    @model_validator(mode="after")
    def _fake_llm_only_locally(self) -> Self:
        # The fake returns scripted output: in a real environment it would turn every
        # document into made-up data, so it never starts there.
        if self.llm_provider == "fake" and (self.app_env is not AppEnv.LOCAL or is_deployed()):
            raise ValueError("LLM_PROVIDER=fake is allowed only with APP_ENV=local, not deployed")
        return self

    @model_validator(mode="after")
    def _require_cloud_settings(self) -> Self:
        if self.app_env is AppEnv.LOCAL:
            if is_deployed():
                # An empty APP_ENV also lands here (env_ignore_empty → default local).
                raise ValueError(
                    "APP_ENV must be dev or prod in a deployed image (empty or local refused)"
                )
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
        url = self.database_url.get_secret_value() if self.database_url else ""
        # parse_qsl decodes %-escapes, so `ssl%6Dode=disable` is seen as sslmode.
        for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=True):
            if key.lower() == "sslmode" and value.lower() in WEAK_SSLMODES:
                # Only a known weak mode is named; the URL is never echoed.
                raise ValueError(f"DATABASE_URL: sslmode={value.lower()} would allow plain text")
            if not is_tls_url_param(key) and key not in ALLOWED_DB_URL_PARAMS:
                raise ValueError(
                    "DATABASE_URL has a query parameter that is not allowed "
                    f"(allowed: {', '.join(sorted(ALLOWED_DB_URL_PARAMS))} and TLS options)"
                )
        try:
            database_ssl_context(self)
        except (ssl.SSLError, ValueError):
            raise ValueError("DATABASE_CA_CERT is not a valid PEM certificate") from None
        return self


def database_ssl_context(settings: Settings) -> ssl.SSLContext | None:
    """TLS for the DB connection: none locally, verify-full everywhere else.

    Without this, asyncpg defaults to `prefer`, which silently falls back to plain text.
    """
    if settings.app_env is AppEnv.LOCAL:
        return None
    if not settings.database_ca_cert or "BEGIN CERTIFICATE" not in settings.database_ca_cert:
        raise ValueError("DATABASE_CA_CERT is required outside local")
    # create_default_context verifies the chain and the hostname (CERT_REQUIRED).
    context = ssl.create_default_context(cadata=settings.database_ca_cert)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


@lru_cache
def get_settings() -> Settings:
    return Settings()

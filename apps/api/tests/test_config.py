import ssl
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from app import config
from app.config import AppEnv, Settings
from tests.ca import make_ca_pem


def test_local_defaults_need_no_env() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_env is AppEnv.LOCAL
    assert settings.database_url is None


@pytest.mark.parametrize("env", ["dev", "prod"])
def test_cloud_env_fails_fast_when_required_settings_missing(
    env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", env)

    with pytest.raises(
        ValidationError, match="DATABASE_URL, SUPABASE_URL, WEB_ORIGIN, DATABASE_CA_CERT"
    ):
        Settings(_env_file=None)


def _cloud_env(monkeypatch: pytest.MonkeyPatch, env: str = "prod") -> None:
    monkeypatch.setenv("APP_ENV", env)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.example:5432/postgres")
    monkeypatch.setenv("DATABASE_CA_CERT", make_ca_pem())
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("WEB_ORIGIN", "https://app.example.com")


def test_cloud_env_accepts_complete_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    _cloud_env(monkeypatch)

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
    _cloud_env(monkeypatch, "dev")
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


def test_env_files_are_found_from_the_package_not_the_cwd() -> None:
    api_dir = Path(config.__file__).resolve().parents[1]

    expected = (api_dir.parent.parent / ".env", api_dir / ".env")
    assert expected == config.ENV_FILES


def test_env_file_is_read_locally(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LOG_LEVEL=DEBUG\n", encoding="utf-8")

    assert Settings(_env_file=env_file).log_level == "DEBUG"


@pytest.mark.parametrize("env", ["dev", "prod", " Dev "])
def test_env_file_is_ignored_when_deployed(
    env: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # L4: a .env baked into an image must not override or fill deployed settings.
    env_file = tmp_path / ".env"
    env_file.write_text(
        "LOG_LEVEL=DEBUG\nDATABASE_URL=postgresql://u:p@baked.example/postgres\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("APP_ENV", env.strip().lower())
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("WEB_ORIGIN", "https://app.example.com")

    with pytest.raises(ValidationError, match="DATABASE_URL"):
        Settings(_env_file=env_file)

    monkeypatch.setenv("APP_ENV", env)
    assert not config.reads_env_files()


@pytest.mark.parametrize("mode", ["disable", "allow", "prefer", "PREFER"])
def test_cloud_env_rejects_weak_sslmode(mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _cloud_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", f"postgresql://u:pw-XYZ@db.example/postgres?sslmode={mode}")

    expected = f"sslmode={mode.lower()} would allow plain text"
    with pytest.raises(ValidationError, match=expected) as excinfo:
        Settings(_env_file=None)
    assert "pw-XYZ" not in str(excinfo.value)


def test_url_encoded_weak_sslmode_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    # `ssl%6Dode` decodes to `sslmode` (0.5c `sec` L1).
    _cloud_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.example/postgres?ssl%6Dode=disable")

    with pytest.raises(ValidationError, match="sslmode=disable would allow plain text"):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "query", ["?SSLMODE=require", "?sslnegotiation=direct", "?application_name=po-api"]
)
def test_tls_options_and_allowed_params_are_accepted(
    query: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cloud_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", f"postgresql://u:p@db.example/postgres{query}")

    assert Settings(_env_file=None).database_url is not None


@pytest.mark.parametrize("query", ["?statement_cache_size=0", "?foo=1", "?pw-XYZ=1"])
def test_unknown_db_url_params_are_rejected_at_startup(
    query: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # They would reach asyncpg.connect() as unknown keywords and fail at the first query,
    # after /health already reported the deploy healthy (0.5c `sec` L1).
    _cloud_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", f"postgresql://u:p@db.example/postgres{query}")

    with pytest.raises(ValidationError, match="query parameter that is not allowed") as excinfo:
        Settings(_env_file=None)
    assert "pw-XYZ" not in str(excinfo.value)


@pytest.mark.parametrize("marker", [("PO_DEPLOYED", "1"), ("RAILWAY_ENVIRONMENT_ID", "abc")])
@pytest.mark.parametrize("app_env", ["", "local", None])
def test_deployed_process_refuses_local(
    marker: tuple[str, str], app_env: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An empty APP_ENV falls back to local, which would turn TLS and docs hiding off
    # (0.5c `sec` M1).
    monkeypatch.setenv(*marker)
    if app_env is not None:
        monkeypatch.setenv("APP_ENV", app_env)

    with pytest.raises(ValidationError, match="APP_ENV must be dev or prod in a deployed image"):
        Settings(_env_file=None)
    assert not config.reads_env_files()


def test_deployed_process_accepts_cloud_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PO_DEPLOYED", "1")
    _cloud_env(monkeypatch, "dev")

    assert Settings(_env_file=None).app_env is AppEnv.DEV


@pytest.mark.parametrize("mode", ["require", "verify-ca", "verify-full"])
def test_cloud_env_accepts_strict_sslmode(mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _cloud_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", f"postgresql://u:p@db.example/postgres?sslmode={mode}")

    assert Settings(_env_file=None).database_url is not None


@pytest.mark.parametrize(
    "pem", ["not a cert", "-----BEGIN CERTIFICATE-----\ngarbage\n-----END CERTIFICATE-----\n"]
)
def test_cloud_env_rejects_invalid_ca_cert(pem: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _cloud_env(monkeypatch)
    monkeypatch.setenv("DATABASE_CA_CERT", pem)

    with pytest.raises(ValidationError, match="DATABASE_CA_CERT is not a valid PEM certificate"):
        Settings(_env_file=None)


def test_database_tls_is_verify_full_outside_local(monkeypatch: pytest.MonkeyPatch) -> None:
    _cloud_env(monkeypatch, "dev")

    context = config.database_ssl_context(Settings(_env_file=None))

    assert context is not None
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_database_tls_is_off_locally() -> None:
    assert config.database_ssl_context(Settings(_env_file=None)) is None


def test_poll_interval_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, worker_poll_interval_seconds=0)

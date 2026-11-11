from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import AppEnv, Settings
from app.main import create_app

# Env vars that would leak a developer's real config into unit tests.
_SETTINGS_ENV_VARS = (
    "APP_ENV",
    "DATABASE_URL",
    "DATABASE_CA_CERT",
    "SUPABASE_URL",
    "WEB_ORIGIN",
    "LOG_LEVEL",
    # Deploy markers: a developer shell or CI runner must not look "deployed".
    "PO_DEPLOYED",
    "RAILWAY_ENVIRONMENT_ID",
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, app_env=AppEnv.LOCAL)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client

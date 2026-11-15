import os
from collections.abc import Iterator
from typing import NoReturn

import httpx2
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
    # LLM: a developer's real key or model must never reach a unit test.
    "LLM_PROVIDER",
    "LLM_API_KEY",
    "LLM_EXTRACTION_MODEL",
    "LLM_MAPPING_MODEL",
    "LLM_TIMEOUT_SECONDS",
    "LLM_MAX_RETRIES",
    "LLM_MAX_OUTPUT_TOKENS",
    "LLM_EFFORT",
    # Read by the anthropic SDK itself.
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_PROFILE",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_CUSTOM_HEADERS",
    "ANTHROPIC_LOG",
    "ANTHROPIC_OPEN_TELEMETRY",
    "ANTHROPIC_OPEN_TELEMETRY_TRACES_CONTENT_MODE",
)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """`live` tests cost money: they run only with RUN_LIVE=1, whatever `-m` says (`sec` L8).

    addopts deselects them, but an explicit `-m integration` replaces addopts' `-m`.
    """
    if os.environ.get("RUN_LIVE", "").strip() == "1":
        return
    skip = pytest.mark.skip(reason="live LLM test: set RUN_LIVE=1 to run it")
    for item in items:
        if item.get_closest_marker("live"):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("live"):
        return  # a live test reads the developer's real LLM_API_KEY on purpose
    for name in _SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


class NetworkBlockedError(AssertionError):
    pass


@pytest.fixture(autouse=True)
def _no_llm_network(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """No unit test can reach the network through the LLM SDK's HTTP stack (httpx2).

    Tests of the adapter inject an `httpx2.MockTransport`, which this leaves alone.
    Only tests marked `live` (never run in CI) may make a real call.
    """
    if request.node.get_closest_marker("live"):
        return

    def _blocked(*_args: object, **_kwargs: object) -> NoReturn:
        raise NetworkBlockedError("network call in a unit test (mark it `live` if intended)")

    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", _blocked)
    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", _blocked)


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, app_env=AppEnv.LOCAL)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client

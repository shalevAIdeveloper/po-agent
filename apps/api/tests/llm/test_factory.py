import httpx2
import pytest
from pydantic import SecretStr, ValidationError

from app.config import AppEnv, Settings
from app.llm.errors import LLMError
from app.llm.factory import create_llm_client
from app.llm.fake import FakeLLMClient
from app.llm.providers.anthropic import AnthropicLLMClient
from tests.conftest import NetworkBlockedError
from tests.llm.helpers import make_request


def _settings(**values: object) -> Settings:
    return Settings(_env_file=None, app_env=AppEnv.LOCAL, **values)  # type: ignore[arg-type]


def test_missing_key_and_model_are_named_not_guessed() -> None:
    with pytest.raises(LLMError) as raised:
        create_llm_client(_settings())
    assert raised.value.code == "llm_config"
    assert "LLM_API_KEY" in str(raised.value)
    assert "LLM_EXTRACTION_MODEL" in str(raised.value)


def test_blank_key_counts_as_missing() -> None:
    settings = _settings(llm_api_key=SecretStr("  "), llm_extraction_model="claude-sonnet-5-5")
    with pytest.raises(LLMError, match="LLM_API_KEY"):
        create_llm_client(settings)


def test_anthropic_client_is_built_with_key_and_model() -> None:
    settings = _settings(
        llm_api_key=SecretStr("sk-ant-x"), llm_extraction_model="claude-sonnet-5-5"
    )
    assert isinstance(create_llm_client(settings), AnthropicLLMClient)


def test_fake_provider_builds_the_fake_locally() -> None:
    assert isinstance(create_llm_client(_settings(llm_provider="fake")), FakeLLMClient)


def test_fake_provider_is_refused_when_deployed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PO_DEPLOYED", "1")
    with pytest.raises(ValidationError, match="LLM_PROVIDER=fake"):
        Settings(_env_file=None, llm_provider="fake")


@pytest.mark.parametrize("env", [AppEnv.DEV, AppEnv.PROD])
def test_fake_provider_is_refused_outside_local(env: AppEnv) -> None:
    with pytest.raises(ValidationError, match="LLM_PROVIDER=fake"):
        Settings(_env_file=None, app_env=env, llm_provider="fake")


def test_llm_settings_defaults_and_secret_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-ant-secret-value")
    settings = Settings(_env_file=None)

    assert settings.llm_provider == "anthropic"
    assert settings.llm_timeout_seconds == 120
    assert settings.llm_max_retries == 2
    assert settings.llm_effort == "medium"
    assert "sk-ant-secret-value" not in repr(settings)


@pytest.mark.parametrize("model", ["Claude Sonnet", "claude sonnet", "", "x" * 101])
def test_model_ids_are_sanity_checked(model: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_extraction_model=model)


async def test_unit_tests_cannot_reach_the_network() -> None:
    async with httpx2.AsyncClient() as client:
        with pytest.raises(NetworkBlockedError):
            await client.get("https://api.anthropic.com/v1/models")


async def test_a_real_adapter_in_a_unit_test_never_gets_a_response() -> None:
    # No http_client injected: the real transport is blocked, so the SDK sees a
    # connection error and nothing leaves the machine.
    settings = _settings(
        llm_api_key=SecretStr("sk-ant-x"), llm_extraction_model="m", llm_max_retries=0
    )
    client = create_llm_client(settings)

    with pytest.raises(LLMError) as raised:
        await client.extract(make_request())
    assert raised.value.code == "llm_unavailable"

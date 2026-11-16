"""Builds the configured LLM client. Called only where an LLM is used (worker, evals --live).

The API process never calls this, so it never needs the key (ADR 0011).
"""

from app.config import Settings
from app.llm.client import LLMClient
from app.llm.errors import LLMError
from app.llm.fake import FakeLLMClient


def create_llm_client(settings: Settings) -> LLMClient:
    if settings.llm_provider == "fake":
        # Settings already refuses `fake` outside local / in a deployed image.
        return FakeLLMClient()
    key = settings.llm_api_key
    missing = []
    if key is None or not key.get_secret_value().strip():
        missing.append("LLM_API_KEY")
    if not settings.llm_extraction_model:
        missing.append("LLM_EXTRACTION_MODEL")
    if missing or key is None:
        raise LLMError("llm_config", detail=f"missing: {', '.join(missing)}")
    # Imported here so the SDK loads only in processes that call the provider.
    from app.llm.providers.anthropic import AnthropicLLMClient

    return AnthropicLLMClient(
        api_key=key,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )

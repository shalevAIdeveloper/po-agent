"""Manual smoke test against the real API. Never runs in CI (deselected by `-m 'not live'`).

Run it yourself, with LLM_API_KEY and LLM_EXTRACTION_MODEL in the repo-root .env:
    uv run pytest -m live tests/llm/test_anthropic_live.py
One tiny call; cost well under $0.01.
"""

import pytest

from app.config import Settings
from app.llm.factory import create_llm_client
from app.llm.types import TextPart
from tests.llm.helpers import Answer, make_request

pytestmark = pytest.mark.live


async def test_live_structured_call() -> None:
    settings = Settings()
    if settings.llm_api_key is None or not settings.llm_extraction_model:
        pytest.skip("LLM_API_KEY / LLM_EXTRACTION_MODEL not set")
    client = create_llm_client(settings)
    request = make_request(
        content=(TextPart("PURCHASE ORDER #123, 2 boxes of gloves"),),
        system="Classify the document. Return only the JSON object.",
        model=settings.llm_extraction_model,
        effort="low",
        max_output_tokens=2000,
    )

    result = await client.extract(request)

    assert result.outcome == "ok", result.validation_errors
    assert isinstance(result.parsed, Answer)
    assert result.provider_request_id
    assert result.input_tokens > 0

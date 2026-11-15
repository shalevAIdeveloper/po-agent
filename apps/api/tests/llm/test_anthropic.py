import asyncio
import base64
import io
import json
from decimal import Decimal
from typing import Any

import httpx2
import pytest
from pydantic import ValidationError

from app.extraction.schema import PurchaseOrderExtraction
from app.llm.errors import LLMError
from app.llm.providers import anthropic as adapter_module
from app.llm.providers.anthropic import REFUSED_ENV_VARS, provider_schema
from app.llm.types import ImagePart, TextPart
from app.logging import configure_logging
from tests.llm.helpers import (
    API_KEY,
    PO_TEXT,
    SYSTEM,
    VALID_OUTPUT,
    Answer,
    adapter,
    body_of,
    make_request,
    message_body,
    ok_response,
)

# ----- request shape -----


async def test_request_is_schema_constrained_without_tools_or_sampling() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return ok_response()

    image = ImagePart(data=b"\x89PNG-bytes", media_type="image/png")
    await adapter(handler).extract(make_request(content=(TextPart(PO_TEXT), image)))

    [request] = seen
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    body = body_of(request)
    assert body["model"] == "claude-sonnet-5-5"
    assert body["max_tokens"] == 1000
    assert body["system"] == SYSTEM
    assert body["output_config"]["effort"] == "medium"
    assert body["output_config"]["format"] == {
        "type": "json_schema",
        "schema": provider_schema(Answer),
    }
    # Document content only in the user turn; no tools, no sampling, no thinking override.
    for absent in ("tools", "tool_choice", "temperature", "top_p", "top_k", "thinking"):
        assert absent not in body
    [message] = body["messages"]
    assert message["role"] == "user"
    text_block, image_block = message["content"]
    assert text_block == {"type": "text", "text": PO_TEXT}
    assert image_block["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": base64.standard_b64encode(b"\x89PNG-bytes").decode(),
    }


async def test_explicit_key_wins_over_sdk_env_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env-must-not-be-used")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token-from-env-must-not-be-used")
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return ok_response()

    await adapter(handler).extract(make_request())

    [request] = seen
    # SDK 2.x sends the key as a bearer credential; only ours, never the env's.
    assert request.headers.get_list("authorization") == [f"Bearer {API_KEY}"]
    assert "from-env" not in str(request.headers.raw)


@pytest.mark.parametrize("name", REFUSED_ENV_VARS)
def test_env_vars_that_redirect_or_log_requests_are_refused(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, "https://attacker.example")

    with pytest.raises(LLMError) as raised:
        adapter(lambda _r: ok_response())

    assert raised.value.code == "llm_config"
    assert name in str(raised.value)
    assert "attacker.example" not in str(raised.value)


def test_empty_key_is_refused() -> None:
    with pytest.raises(LLMError) as raised:
        adapter(lambda _r: ok_response(), api_key="   ")
    assert raised.value.code == "llm_config"


# ----- results -----


async def test_valid_output_is_parsed_with_usage_cost_and_request_id() -> None:
    result = await adapter(lambda _r: ok_response(request_id="req_abc")).extract(make_request())

    assert result.outcome == "ok"
    assert result.parsed == Answer(kind="po", score=0.9)
    assert result.raw_output == VALID_OUTPUT  # thinking blocks are skipped
    assert result.provider == "anthropic"
    assert result.model == "claude-sonnet-5-5"
    assert result.provider_request_id == "req_abc"
    assert (result.input_tokens, result.output_tokens) == (1000, 200)
    # 1000 * $2/M + 200 * $10/M
    assert result.cost_usd == Decimal("0.004000")
    assert result.latency_ms >= 0


async def test_invalid_output_is_a_result_with_sanitized_errors() -> None:
    bad = json.dumps({"kind": PO_TEXT, "score": 7, "smuggled": PO_TEXT})
    result = await adapter(lambda _r: ok_response(message_body(bad))).extract(make_request())

    assert result.outcome == "invalid_output"
    assert result.parsed is None
    assert result.cost_usd is not None  # the call was billed
    paths = {issue.path for issue in result.validation_errors}
    assert paths == {"kind", "score", "smuggled"}
    assert PO_TEXT not in repr(result.validation_errors)


@pytest.mark.parametrize(
    ("stop_reason", "outcome"),
    [
        ("refusal", "refusal"),
        ("max_tokens", "truncated"),
        ("model_context_window_exceeded", "truncated"),
    ],
)
async def test_refusal_and_truncation_are_never_parsed(stop_reason: str, outcome: str) -> None:
    body = message_body(VALID_OUTPUT, stop_reason=stop_reason)
    result = await adapter(lambda _r: ok_response(body)).extract(make_request())

    assert result.outcome == outcome
    assert result.parsed is None
    assert result.stop_reason == stop_reason


async def test_unknown_model_has_no_cost() -> None:
    body = message_body(model="claude-unknown-9")
    result = await adapter(lambda _r: ok_response(body)).extract(make_request())

    assert result.outcome == "ok"
    assert result.cost_usd is None


# ----- transport errors and retries -----


async def test_429_is_retried_honouring_retry_after() -> None:
    calls: list[int] = []

    def handler(_request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx2.Response(429, json={"type": "error"}, headers={"retry-after-ms": "1"})
        return ok_response()

    result = await adapter(handler, max_retries=2).extract(make_request())

    assert result.outcome == "ok"
    assert len(calls) == 2


async def test_429_after_all_retries_is_rate_limited() -> None:
    calls: list[int] = []

    def handler(_request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        return httpx2.Response(429, json={"type": "error"}, headers={"retry-after-ms": "1"})

    with pytest.raises(LLMError) as raised:
        await adapter(handler, max_retries=2).extract(make_request())

    assert raised.value.code == "llm_rate_limited"
    assert raised.value.transient
    assert len(calls) == 3


async def test_400_is_not_retried_and_hides_the_provider_message() -> None:
    calls: list[int] = []
    error_body = {"type": "error", "error": {"type": "invalid_request_error", "message": PO_TEXT}}

    def handler(_request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        return httpx2.Response(400, json=error_body, headers={"request-id": "req_bad"})

    with pytest.raises(LLMError) as raised:
        await adapter(handler, max_retries=2).extract(make_request())

    error = raised.value
    assert len(calls) == 1
    assert error.code == "llm_bad_request"
    assert not error.transient
    assert error.status_code == 400
    assert error.provider_request_id == "req_bad"
    assert PO_TEXT not in str(error)
    assert error.__cause__ is None
    assert error.__suppress_context__


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "llm_auth"),
        (403, "llm_auth"),
        (413, "llm_bad_request"),
        (500, "llm_unavailable"),
        (529, "llm_unavailable"),
    ],
)
async def test_status_codes_map_to_stable_codes(status: int, code: str) -> None:
    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, json={"type": "error"})

    with pytest.raises(LLMError) as raised:
        await adapter(handler, max_retries=0).extract(make_request())
    assert raised.value.code == code


async def test_transport_timeout_is_llm_timeout() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("timed out", request=request)

    with pytest.raises(LLMError) as raised:
        await adapter(handler, max_retries=0).extract(make_request())
    assert raised.value.code == "llm_timeout"


async def test_connection_error_is_unavailable() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    with pytest.raises(LLMError) as raised:
        await adapter(handler, max_retries=0).extract(make_request())
    assert raised.value.code == "llm_unavailable"


async def test_whole_call_is_bounded_by_a_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    # The SDK waits as long as `retry-after` says; the adapter's deadline still applies.
    monkeypatch.setattr(adapter_module, "DEADLINE_SLACK_SECONDS", 0.0)

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        await asyncio.sleep(5)
        return ok_response()

    client = adapter(handler, timeout_seconds=0.05, max_retries=0)
    with pytest.raises(LLMError) as raised:
        await client.extract(make_request())
    assert raised.value.code == "llm_timeout"


# ----- logging -----


def _lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


async def test_log_line_has_accounting_and_no_prompt_or_output() -> None:
    stream = io.StringIO()
    configure_logging(stream=stream)

    await adapter(lambda _r: ok_response(request_id="req_log")).extract(make_request())

    output = stream.getvalue()
    for leaked in (SYSTEM, PO_TEXT, VALID_OUTPUT, API_KEY, '"po"'):
        assert leaked not in output
    [line] = [entry for entry in _lines(stream) if entry["event"] == "llm.call"]
    assert line == line | {
        "provider": "anthropic",
        "model": "claude-sonnet-5-5",
        "prompt_version": "extract_po/v1",
        "schema_version": "answer.v1",
        "outcome": "ok",
        "input_tokens": 1000,
        "output_tokens": 200,
        "cost_usd": "0.004000",
        "provider_request_id": "req_log",
        "validation_error_count": 0,
    }


async def test_failure_log_line_has_code_and_no_provider_text() -> None:
    stream = io.StringIO()
    configure_logging(stream=stream)
    error_body = {"type": "error", "error": {"type": "x", "message": PO_TEXT}}

    with pytest.raises(LLMError):
        await adapter(lambda _r: httpx2.Response(400, json=error_body)).extract(make_request())

    assert PO_TEXT not in stream.getvalue()
    [line] = [entry for entry in _lines(stream) if entry["event"] == "llm.call"]
    assert line["outcome"] == "llm_bad_request"


# ----- schema sent to the provider -----

_UNSUPPORTED = (
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "pattern",
    "minItems",
    "maxItems",
)


def _walk(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        found.append(node)
        for value in node.values():
            found.extend(_walk(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_walk(value))
    return found


def test_extraction_schema_is_in_the_providers_supported_subset() -> None:
    schema = provider_schema(PurchaseOrderExtraction)

    for node in _walk(schema):
        for keyword in _UNSUPPORTED:
            assert keyword not in node
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False
            assert set(node.get("required", [])) == set(node.get("properties", {}))


def test_dropped_constraints_are_still_enforced_by_our_model() -> None:
    # The API no longer sees `maximum: 1` on confidence or the decimal pattern; Pydantic does.
    with pytest.raises(ValidationError):
        Answer.model_validate_json('{"kind": "po", "score": 1.5}')

"""The only way the app calls an LLM (CLAUDE.md): `LLMClient.extract`.

Shared by every implementation (provider adapter, fake, replay), so a fake or replayed
response goes through exactly the same parsing, cost and logging as a live one.
"""

from typing import Any, Final, Protocol

import structlog
from pydantic import BaseModel, ValidationError

from app.llm.pricing import cost_usd
from app.llm.types import LLMResult, Outcome, StructuredRequest, ValidationIssue

log = structlog.get_logger("app.llm")

# Bounds on what a sanitized error may carry: the path can contain key names the model
# invented (`extra_forbidden`), which may come from document text.
MAX_ISSUES: Final = 50
MAX_PATH_SEGMENT: Final = 64
MAX_MESSAGE: Final = 300


class LLMClient(Protocol):
    provider: str

    async def extract[T: BaseModel](self, request: StructuredRequest[T]) -> LLMResult[T]: ...


def _segment(part: str | int) -> str:
    text = str(part)
    return text if len(text) <= MAX_PATH_SEGMENT else text[:MAX_PATH_SEGMENT] + "…"


def sanitize(error: ValidationError) -> tuple[ValidationIssue, ...]:
    """Field paths + messages for the schema retry (PR 3) and storage; never the input."""
    issues = []
    for item in error.errors(include_input=False, include_url=False, include_context=False):
        issues.append(
            ValidationIssue(
                path=".".join(_segment(part) for part in item["loc"]),
                type=item["type"],
                message=item["msg"][:MAX_MESSAGE],
            )
        )
    return tuple(issues[:MAX_ISSUES])


def build_result[T: BaseModel](
    request: StructuredRequest[T],
    *,
    provider: str,
    model: str,
    raw_output: str | None,
    stop_reason: str | None,
    provider_request_id: str | None,
    latency_ms: int,
    input_tokens: int,
    output_tokens: int,
) -> LLMResult[T]:
    parsed: T | None = None
    issues: tuple[ValidationIssue, ...] = ()
    outcome: Outcome
    if stop_reason == "refusal":
        outcome = "refusal"
    elif stop_reason in ("max_tokens", "model_context_window_exceeded"):
        outcome = "truncated"  # the JSON is incomplete: never parsed, never half-used
    elif raw_output is None:
        outcome = "invalid_output"
        issues = (ValidationIssue(path="", type="missing_output", message="no text output"),)
    else:
        try:
            parsed = request.output_model.model_validate_json(raw_output)
            outcome = "ok"
        except ValidationError as error:
            outcome = "invalid_output"
            issues = sanitize(error)
    cost = cost_usd(model, input_tokens, output_tokens)
    if cost is None:
        log.warning("llm.unknown_model_price", provider=provider, model=model)
    result = LLMResult(
        outcome=outcome,
        parsed=parsed,
        validation_errors=issues,
        raw_output=raw_output,
        provider=provider,
        model=model,
        provider_request_id=provider_request_id,
        stop_reason=stop_reason,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
    )
    log_call(request, result)
    return result


def log_call(request: StructuredRequest[Any], result: LLMResult[Any]) -> None:
    """One line per call: accounting only, no prompt, content, output or error text."""
    log.info(
        "llm.call",
        provider=result.provider,
        model=result.model,
        prompt_version=request.prompt_version,
        schema_version=request.schema_version,
        outcome=result.outcome,
        stop_reason=result.stop_reason,
        latency_ms=result.latency_ms,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=None if result.cost_usd is None else str(result.cost_usd),
        validation_error_count=len(result.validation_errors),
        provider_request_id=result.provider_request_id,
    )


def log_failure(
    request: StructuredRequest[Any], *, provider: str, code: str, latency_ms: int
) -> None:
    log.warning(
        "llm.call",
        provider=provider,
        model=request.model,
        prompt_version=request.prompt_version,
        schema_version=request.schema_version,
        outcome=code,
        latency_ms=latency_ms,
    )

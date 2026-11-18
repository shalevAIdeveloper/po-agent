import json

import structlog
from pydantic import BaseModel, ConfigDict

from app.extraction.structured_call import RecordedCall, call_with_retry
from app.llm.fake import FakeLLMClient, FakeResponse
from app.llm.prompts import parse_prompt
from app.llm.types import StructuredRequest, TextPart, ValidationIssue

PROMPT = parse_prompt(
    "demo",
    1,
    "## System\nAnswer.\n\n## User\nGo.\n\n## Retry\nFix:\n{{validation_errors}}\n",
)


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


REQUEST = StructuredRequest(
    system=PROMPT.system,
    content=(TextPart("Go."),),
    output_model=Answer,
    prompt_version="demo/v1",
    schema_version="answer.v1",
    model="m",
    max_output_tokens=100,
)


def ok(value: int) -> FakeResponse:
    return FakeResponse(json.dumps({"value": value}))


def positive(answer: Answer) -> tuple[ValidationIssue, ...]:
    if answer.value > 0:
        return ()
    return (ValidationIssue(path="value", type="not_positive", message="must be positive"),)


async def test_ok_first_time() -> None:
    outcome = await call_with_retry(FakeLLMClient([ok(3)]), PROMPT, REQUEST, "map_columns")
    assert outcome.failure is None and outcome.parsed == Answer(value=3)
    assert [(c.purpose, c.attempt) for c in outcome.calls] == [("map_columns", 1)]
    assert isinstance(outcome.calls[0], RecordedCall)


async def test_a_check_failure_is_retried_once_with_the_issue() -> None:
    client = FakeLLMClient([ok(-1), ok(2)])
    outcome = await call_with_retry(client, PROMPT, REQUEST, "map_columns", positive)
    assert outcome.parsed == Answer(value=2)
    assert [c.attempt for c in outcome.calls] == [1, 2]
    retry = client.requests[1].content[-1]
    assert isinstance(retry, TextPart) and "not_positive" in retry.text
    assert client.requests[1].content[:-1] == REQUEST.content


async def test_a_check_failure_twice_is_schema_validation_failed() -> None:
    client = FakeLLMClient([ok(-1), ok(0)])
    outcome = await call_with_retry(client, PROMPT, REQUEST, "map_columns", positive)
    assert (outcome.failure, outcome.parsed, len(outcome.calls)) == (
        "schema_validation_failed",
        None,
        2,
    )


async def test_a_bug_in_the_check_keeps_the_call_and_is_not_retried() -> None:
    def broken(_answer: Answer) -> tuple[ValidationIssue, ...]:
        raise KeyError("SECRET-IN-MESSAGE")

    client = FakeLLMClient([ok(1), ok(1)])
    with structlog.testing.capture_logs() as logs:
        outcome = await call_with_retry(client, PROMPT, REQUEST, "map_columns", broken)
    assert outcome.failure == "schema_validation_failed"
    assert len(outcome.calls) == 1 and len(client.requests) == 1
    assert {"event": "extraction.check_failed", "error_type": "KeyError"}.items() <= next(
        entry for entry in logs if entry["event"] == "extraction.check_failed"
    ).items()
    assert "SECRET-IN-MESSAGE" not in repr(logs)

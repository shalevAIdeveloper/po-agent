"""One structured LLM call with the schema retry, shared by every prompt (M1 PR 5 T8).

- `call_with_retry` makes the call; an `invalid_output` answer, or an answer our own
  `check` refuses (column mapping: references outside the sample, ...), is retried once
  with the sanitized issues (paths, types, fixed messages; never input values) in the
  prompt's `Retry` section, inside a `<validation_errors>` data block (SPEC section 9,
  Follow-up M1-L3). A second bad answer is `schema_validation_failed`.
- `refusal` and `truncated` are final: no retry, no fallback model (ADR 0011).
- Every billed call is kept as a `RecordedCall(purpose, attempt, result)`; the retry is
  `attempt=2` of the same purpose. A transport `LLMError` is raised as
  `ExtractionCallError` with the calls already paid for, so the worker (PR 14) can store
  every one.
"""

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, Literal

import structlog
from pydantic import BaseModel

from app.llm.client import LLMClient
from app.llm.errors import LLMError
from app.llm.prompts import Prompt
from app.llm.types import LLMResult, StructuredRequest, TextPart, ValidationIssue

log = structlog.get_logger("app.extraction")

CallPurpose = Literal["extract", "map_columns"]
FailureCode = Literal["schema_validation_failed", "llm_refusal", "llm_truncated"]
WITHHELD_KEY: Final = "(a key not in the schema; name withheld)"
FIRST_ATTEMPT: Final = 1
RETRY_ATTEMPT: Final = 2

# Our own check of a schema-valid answer: issues to send back, () if it is fine.
Check = Callable[[Any], tuple[ValidationIssue, ...]]


@dataclass(frozen=True, slots=True)
class RecordedCall[T: BaseModel]:
    """A billed call and why it was made. `LLMResult` hides its content from repr."""

    purpose: CallPurpose
    attempt: int  # 1, or 2 for the schema retry
    result: LLMResult[T]


@dataclass(frozen=True, slots=True)
class CallOutcome[T: BaseModel]:
    parsed: T | None = dataclasses.field(repr=False)  # set exactly when failure is None
    failure: FailureCode | None
    calls: tuple[RecordedCall[T], ...]


class ExtractionCallError(Exception):
    """An `LLMError` during extraction, with the calls made (and billed) before it."""

    def __init__(self, error: LLMError, calls: tuple[RecordedCall[Any], ...]) -> None:
        super().__init__(error.code)
        self.error = error
        self.calls = calls


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _issue_path(issue: ValidationIssue) -> str:
    if issue.type == "extra_forbidden":
        # The key name is model-chosen and may echo document text; the model doesn't need
        # it to drop the key (`sec` L4). Every other path is our schema's field names.
        return WITHHELD_KEY
    return _escape(issue.path) or "(root)"


def format_issues(issues: tuple[ValidationIssue, ...]) -> str:
    """One line per issue: field path, type and a fixed message; never input values."""
    return "\n".join(
        f"- path: {_issue_path(issue)}; type: {_escape(issue.type)}; "
        f"message: {_escape(issue.message)}"
        for issue in issues
    )


class _CheckFailedError(Exception):
    """Our `check` raised: a bug on our side, never retried (it would be paid again)."""


def _issues[T: BaseModel](
    result: LLMResult[T], check: Check | None
) -> tuple[ValidationIssue, ...] | None:
    """Why the answer can't be used (and may be retried), or None if it can."""
    if result.outcome == "invalid_output":
        return result.validation_errors
    if result.outcome == "ok" and result.parsed is not None and check is not None:
        try:
            return check(result.parsed) or None
        except Exception as error:
            log.error("extraction.check_failed", error_type=type(error).__name__)
            raise _CheckFailedError from None
    return None


async def call_with_retry[T: BaseModel](
    client: LLMClient,
    prompt: Prompt,
    request: StructuredRequest[T],
    purpose: CallPurpose,
    check: Check | None = None,
) -> CallOutcome[T]:
    # A broken Retry section must fail before the first (billed) call, not after it.
    prompt.render("Retry", validation_errors="")
    calls: list[RecordedCall[T]] = []

    async def call(req: StructuredRequest[T], attempt: int) -> LLMResult[T]:
        try:
            result = await client.extract(req)
        except LLMError as error:
            raise ExtractionCallError(error, tuple(calls)) from error
        calls.append(RecordedCall(purpose=purpose, attempt=attempt, result=result))
        return result

    failed = CallOutcome[T](parsed=None, failure="schema_validation_failed", calls=())
    last = await call(request, FIRST_ATTEMPT)
    try:
        issues = _issues(last, check)
        if issues is not None:
            retry = TextPart(prompt.render("Retry", validation_errors=format_issues(issues)))
            last = await call(
                dataclasses.replace(request, content=(*request.content, retry)), RETRY_ATTEMPT
            )
            issues = _issues(last, check)
    except _CheckFailedError:
        return dataclasses.replace(failed, calls=tuple(calls))
    failure: FailureCode | None = None
    if last.outcome == "refusal":
        failure = "llm_refusal"
    elif last.outcome == "truncated":
        failure = "llm_truncated"
    elif issues is not None or last.outcome != "ok" or last.parsed is None:
        failure = "schema_validation_failed"
    return CallOutcome(parsed=None if failure else last.parsed, failure=failure, calls=tuple(calls))

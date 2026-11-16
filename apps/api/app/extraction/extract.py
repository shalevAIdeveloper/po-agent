"""Extraction core: one document in, a validated `PurchaseOrderExtraction` or a failure out.

- The prompt is `extract_po/v1`. The document goes only into the user turn, between
  `<document>` and `</document>`; delimiter-like text inside it is neutralized, so the
  document can't close the block or open a new one (SPEC section 9, principle 3). Metadata
  (sender, received time) is untrusted too: one short line each, inside the block.
- Schema retry once (SPEC section 9): an `invalid_output` answer is retried with the sanitized
  validation errors (paths, types, fixed messages; never input values) appended as a
  second text part inside a `<validation_errors>` data block (Follow-up M1-L3). A second
  invalid answer is `failed` / `schema_validation_failed`.
- `refusal` and `truncated` are final: no retry, no fallback model (ADR 0011).
- Transport errors (`LLMError`) are raised as `ExtractionCallError`, which keeps the calls
  already paid for so the worker (PR 14) can store every one.
- Grounding (Q16) adds `ungrounded` warnings for text inputs; it never changes a value.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Literal

from app.extraction.grounding import ExtractionWarning, check_grounding
from app.extraction.schema import SCHEMA_VERSION, PurchaseOrderExtraction
from app.llm.client import LLMClient
from app.llm.errors import LLMError
from app.llm.prompts import Prompt, load_prompt
from app.llm.types import (
    ContentPart,
    Effort,
    ImagePart,
    LLMResult,
    StructuredRequest,
    TextPart,
    ValidationIssue,
)

PROMPT_NAME: Final = "extract_po"
PROMPT_VERSION: Final = 1
MAX_EMAIL_CHARS: Final = 254
MAX_RECEIVED_AT_CHARS: Final = 64
UNKNOWN: Final = "unknown"

# Invisible characters an attacker could put inside a delimiter to hide it from a naive
# match while a model may still read it as the delimiter.
# Built from code points so the source stays ASCII (soft hyphen, Mongolian vowel
# separator, zero-width and bidi marks, word joiners, BOM).
_INVISIBLE_CODES: Final = (
    0x00AD,
    0x180E,
    *range(0x200B, 0x2010),
    *range(0x202A, 0x202F),
    *range(0x2060, 0x2065),
    0xFEFF,
)
_GAP: Final = "[\\s" + "".join(chr(code) for code in _INVISIBLE_CODES) + "]*"
_DELIMITER_RE: Final = re.compile(
    rf"<{_GAP}/?{_GAP}(?:document|metadata|validation_errors)\b", re.IGNORECASE
)
_ROLE_HEADING_RE: Final = re.compile(
    r"^([ \t]*)(#+[ \t]*(?:system|user|assistant|retry)\b)", re.IGNORECASE | re.MULTILINE
)
# C0 controls except tab and newline, DEL, and C1 controls.
_CONTROL_RE: Final = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

FailureCode = Literal["schema_validation_failed", "llm_refusal", "llm_truncated"]


@dataclass(frozen=True, slots=True)
class DocumentInput:
    """What the parsers (PR 4/5) hand over. Content never appears in a repr."""

    text: str | None = field(default=None, repr=False)  # text layer; None for image-only
    images: tuple[ImagePart, ...] = field(default=(), repr=False)
    sender_email: str | None = field(default=None, repr=False)
    received_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.has_text and not self.images:
            raise ValueError("a document needs text or images")

    @property
    def has_text(self) -> bool:
        return self.text is not None and bool(self.text.strip())


@dataclass(frozen=True, slots=True)
class ExtractionSettings:
    model: str
    max_output_tokens: int
    effort: Effort = "medium"


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    status: Literal["extracted", "failed"]
    extraction: PurchaseOrderExtraction | None = field(repr=False)
    error_code: FailureCode | None
    warnings: tuple[ExtractionWarning, ...]
    calls: tuple[LLMResult[PurchaseOrderExtraction], ...]  # every billed call, in order
    prompt_version: str
    prompt_sha256: str


class ExtractionCallError(Exception):
    """An `LLMError` during extraction, with the calls made (and billed) before it."""

    def __init__(
        self, error: LLMError, calls: tuple[LLMResult[PurchaseOrderExtraction], ...]
    ) -> None:
        super().__init__(error.code)
        self.error = error
        self.calls = calls


def neutralize(text: str) -> str:
    """Document text made safe to place between our delimiters (otherwise unchanged)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_RE.sub("", text)
    text = _DELIMITER_RE.sub(lambda match: "&lt;" + match.group(0)[1:], text)
    return _ROLE_HEADING_RE.sub(lambda match: match.group(1) + "\\" + match.group(2), text)


def _metadata(value: str | None, limit: int) -> str:
    if value is None:
        return UNKNOWN
    # One line: every control/format character (newlines, bidi, zero-width) becomes a space.
    flat = "".join(" " if unicodedata.category(ch).startswith("C") else ch for ch in value)
    flat = neutralize(" ".join(flat.split()))[:limit].strip()
    return flat or UNKNOWN


def build_content(prompt: Prompt, document: DocumentInput) -> tuple[ContentPart, ...]:
    received_at = None if document.received_at is None else document.received_at.isoformat()
    before, after = prompt.split(
        "User",
        "document",
        sender_email=_metadata(document.sender_email, MAX_EMAIL_CHARS),
        received_at=_metadata(received_at, MAX_RECEIVED_AT_CHARS),
    )
    text = neutralize(document.text) if document.has_text and document.text else ""
    if not document.images:
        return (TextPart(before + text + after),)
    lead = before + text + ("\n" if text else "")
    return (TextPart(lead), *document.images, TextPart(after))


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_issues(issues: tuple[ValidationIssue, ...]) -> str:
    """One line per issue; paths may hold model-invented key names, so `<`/`>` are escaped."""
    return "\n".join(
        f"- path: {_escape(issue.path) or '(root)'}; type: {_escape(issue.type)}; "
        f"message: {_escape(issue.message)}"
        for issue in issues
    )


def _failure(
    result: LLMResult[PurchaseOrderExtraction],
) -> FailureCode:
    if result.outcome == "refusal":
        return "llm_refusal"
    if result.outcome == "truncated":
        return "llm_truncated"
    return "schema_validation_failed"


async def extract_po(
    client: LLMClient, document: DocumentInput, settings: ExtractionSettings
) -> ExtractionResult:
    prompt = load_prompt(PROMPT_NAME, PROMPT_VERSION)
    request = StructuredRequest(
        system=prompt.system,
        content=build_content(prompt, document),
        output_model=PurchaseOrderExtraction,
        prompt_version=prompt.prompt_version,
        schema_version=SCHEMA_VERSION,
        model=settings.model,
        max_output_tokens=settings.max_output_tokens,
        effort=settings.effort,
    )
    calls: list[LLMResult[PurchaseOrderExtraction]] = []

    async def call(req: StructuredRequest[PurchaseOrderExtraction]) -> None:
        try:
            calls.append(await client.extract(req))
        except LLMError as error:
            raise ExtractionCallError(error, tuple(calls)) from error

    await call(request)
    if calls[-1].outcome == "invalid_output":
        retry = TextPart(
            prompt.render("Retry", validation_errors=format_issues(calls[-1].validation_errors))
        )
        await call(
            StructuredRequest(
                system=request.system,
                content=(*request.content, retry),
                output_model=request.output_model,
                prompt_version=request.prompt_version,
                schema_version=request.schema_version,
                model=request.model,
                max_output_tokens=request.max_output_tokens,
                effort=request.effort,
            )
        )

    last = calls[-1]
    if last.outcome != "ok" or last.parsed is None:
        return ExtractionResult(
            status="failed",
            extraction=None,
            error_code=_failure(last),
            warnings=(),
            calls=tuple(calls),
            prompt_version=prompt.prompt_version,
            prompt_sha256=prompt.sha256,
        )
    warnings = (
        check_grounding(last.parsed, document.text) if document.has_text and document.text else ()
    )
    return ExtractionResult(
        status="extracted",
        extraction=last.parsed,
        error_code=None,
        warnings=warnings,
        calls=tuple(calls),
        prompt_version=prompt.prompt_version,
        prompt_sha256=prompt.sha256,
    )

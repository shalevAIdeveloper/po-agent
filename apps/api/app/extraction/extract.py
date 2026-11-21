"""Extraction core: one document in, a validated `PurchaseOrderExtraction` or a failure out.

- The prompt is `extract_po/v1`. The document goes only into the user turn, between
  `<document>` and `</document>`; delimiter-like text inside it is neutralized, so the
  document can't close the block or open a new one (SPEC section 9, principle 3). Metadata
  (sender, received time) is untrusted too: one short line each, inside the block.
- The call and its schema retry go through `structured_call.call_with_retry` (shared with
  the spreadsheet column mapping): `invalid_output` is retried once with the sanitized
  validation errors; `refusal` and `truncated` are final; every billed call is kept as a
  `RecordedCall` (purpose `extract`, attempt 1 or 2), also on a transport error
  (`ExtractionCallError`).
- Grounding (Q16) adds `ungrounded` warnings for text inputs; it never changes a value.
"""

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Literal

import structlog

from app.extraction.grounding import ExtractionWarning, check_grounding
from app.extraction.schema import SCHEMA_VERSION, PurchaseOrderExtraction
from app.extraction.structured_call import (
    WITHHELD_KEY,
    ExtractionCallError,
    FailureCode,
    RecordedCall,
    call_with_retry,
    format_issues,
)
from app.llm.client import LLMClient
from app.llm.prompts import Prompt, load_prompt
from app.llm.types import ContentPart, Effort, ImagePart, StructuredRequest, TextPart

__all__ = [
    "WITHHELD_KEY",
    "DocumentInput",
    "ExtractionCallError",
    "ExtractionResult",
    "ExtractionSettings",
    "FailureCode",
    "build_content",
    "document_id",
    "extract_po",
    "format_issues",
    "neutralize",
]

log = structlog.get_logger("app.extraction")

PROMPT_NAME: Final = "extract_po"
DOCUMENT_ID_CHARS: Final = 32  # 128 bits
PROMPT_VERSION: Final = 1
MAX_EMAIL_CHARS: Final = 254
MAX_RECEIVED_AT_CHARS: Final = 64
UNKNOWN: Final = "unknown"

MAX_CODE_POINT: Final = 0x10FFFF
# Characters an attacker could put inside a delimiter to hide it from a plain match while
# a model may still read it as the delimiter: every format (Cf: zero-width, bidi, soft
# hyphen, BOM, tag characters) and space/separator (Zs/Zl/Zp) character, plus the
# combining grapheme joiner and variation selectors. Built from code points at import, so
# the source stays ASCII and the set can't silently miss a new range (`sec` M2).
_INVISIBLE_CATEGORIES: Final = frozenset({"Cf", "Zs", "Zl", "Zp"})
_EXTRA_INVISIBLE: Final = (0x034F, *range(0xFE00, 0xFE10), *range(0xE0100, 0xE01F0))
_INVISIBLE: Final = "".join(
    re.escape(chr(code))
    for code in sorted(
        {
            code
            for code in range(MAX_CODE_POINT + 1)
            if unicodedata.category(chr(code)) in _INVISIBLE_CATEGORIES
        }
        | set(_EXTRA_INVISIBLE)
    )
)
# Look-alikes a model may read as "<" / "/" (fullwidth, small form, angle brackets, math).
_LESS_THAN: Final = "".join(
    re.escape(chr(code)) for code in (0x3C, 0xFF1C, 0xFE64, 0x2329, 0x27E8, 0x3008)
)
_SLASH: Final = "".join(re.escape(chr(code)) for code in (0x2F, 0xFF0F, 0x2215, 0x2044, 0x29F8))
_HASH: Final = "".join(re.escape(chr(code)) for code in (0x23, 0xFF03))
# Possessive (`*+`): never gives characters back, so matching stays linear on hostile
# input; the old `<\s*/?\s*` backtracked quadratically (`sec` H1).
_GAP: Final = f"[\\s{_INVISIBLE}]*+"
_INLINE_GAP: Final = f"[ \\t{_INVISIBLE}]*+"


def _spaced(word: str) -> str:
    """`word` with invisible characters or spaces allowed between its letters."""
    return _GAP.join(re.escape(letter) for letter in word)


def delimiter_pattern(*names: str) -> re.Pattern[str]:
    """Opening or closing tags named `names`, with look-alike and invisible tricks."""
    return re.compile(
        f"[{_LESS_THAN}]{_GAP}(?:[{_SLASH}]{_GAP})?(?:{'|'.join(_spaced(n) for n in names)})",
        re.IGNORECASE,
    )


_DELIMITER_RE: Final = delimiter_pattern("document", "metadata", "validation_errors")
_ROLE_HEADING_RE: Final = re.compile(
    f"^({_INLINE_GAP})([{_HASH}]{_INLINE_GAP}(?:[{_HASH}]{_INLINE_GAP})*+"
    "(?:system|user|assistant|retry)(?![a-z0-9_]))",
    re.IGNORECASE | re.MULTILINE,
)
# C0 controls except tab and newline, DEL, and C1 controls.
_CONTROL_RE: Final = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


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
    effort: Effort | None = "medium"  # None: not sent (the model default)


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    status: Literal["extracted", "failed"]
    extraction: PurchaseOrderExtraction | None = field(repr=False)
    error_code: FailureCode | None
    warnings: tuple[ExtractionWarning, ...]
    # Every billed call, in order (`extract`, or `map_columns` for a spreadsheet).
    calls: tuple[RecordedCall[Any], ...]
    prompt_version: str
    prompt_sha256: str
    # False for image-only input or if the check failed: "no warnings" then means "not
    # checked", never "all grounded" (`sec` L2).
    grounding_checked: bool
    # Spreadsheets: the cell each header fact and address line came from (`sec` M5).
    # Beside the extraction, not in it: `po_extraction.v1` is unchanged.
    sources: tuple["ValueSource", ...] = ()


@dataclass(frozen=True, slots=True)
class ValueSource:
    """Where a value was read: a field path and a sheet cell (row number, column letter)."""

    path: str
    row: int
    column: str


def neutralize(text: str, delimiters: re.Pattern[str] = _DELIMITER_RE) -> str:
    """Document text made safe to place between our delimiters (otherwise unchanged)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_RE.sub("", text)
    text = delimiters.sub(lambda match: "&lt;" + match.group(0)[1:], text)
    return _ROLE_HEADING_RE.sub(lambda match: match.group(1) + "\\" + match.group(2), text)


def _metadata(value: str | None, limit: int) -> str:
    if value is None:
        return UNKNOWN
    # One line: every control/format character (newlines, bidi, zero-width) becomes a space.
    flat = "".join(" " if unicodedata.category(ch).startswith("C") else ch for ch in value)
    flat = neutralize(" ".join(flat.split()))[:limit].strip()
    return flat or UNKNOWN


def document_id(document: DocumentInput) -> str:
    """A code derived from the whole input, used in the delimiter tags.

    The document can't contain the hash of itself, so it can't forge the closing tag,
    whatever look-alike tricks it uses; this also covers text printed on page images.
    Deterministic, so replay cassettes still match.
    """
    received_at = None if document.received_at is None else document.received_at.isoformat()
    payload = {
        "v": 1,
        "text": document.text,
        "images": [
            [image.media_type, hashlib.sha256(image.data).hexdigest()] for image in document.images
        ],
        "sender_email": document.sender_email,
        "received_at": received_at,
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()[:DOCUMENT_ID_CHARS]


def build_content(prompt: Prompt, document: DocumentInput) -> tuple[ContentPart, ...]:
    received_at = None if document.received_at is None else document.received_at.isoformat()
    before, after = prompt.split(
        "User",
        "document",
        document_id=document_id(document),
        sender_email=_metadata(document.sender_email, MAX_EMAIL_CHARS),
        received_at=_metadata(received_at, MAX_RECEIVED_AT_CHARS),
    )
    text = neutralize(document.text) if document.has_text and document.text else ""
    if not document.images:
        return (TextPart(before + text + after),)
    lead = before + text + ("\n" if text else "")
    return (TextPart(lead), *document.images, TextPart(after))


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
    outcome = await call_with_retry(client, prompt, request, "extract")
    calls: tuple[RecordedCall[Any], ...] = outcome.calls
    if outcome.failure is not None or outcome.parsed is None:
        return ExtractionResult(
            status="failed",
            extraction=None,
            error_code=outcome.failure or "schema_validation_failed",
            warnings=(),
            calls=calls,
            prompt_version=prompt.prompt_version,
            prompt_sha256=prompt.sha256,
            grounding_checked=False,
        )
    extraction = outcome.parsed
    warnings: tuple[ExtractionWarning, ...] = ()
    grounding_checked = False
    if document.has_text and document.text is not None:
        try:
            warnings = check_grounding(extraction, document.text)
            grounding_checked = True
        except Exception as error:
            # The calls are paid for: a bug in a flag-only check must never lose them
            # (`sec` M1). The reviewer sees "not checked".
            log.warning("extraction.grounding_failed", error_type=type(error).__name__)
    return ExtractionResult(
        status="extracted",
        extraction=extraction,
        error_code=None,
        warnings=warnings,
        calls=calls,
        prompt_version=prompt.prompt_version,
        prompt_sha256=prompt.sha256,
        grounding_checked=grounding_checked,
    )

"""Untrusted file bytes in, a `ParsedDocument` (or an `InputError` code) out (M1 PR 4, ADR 0012).

- `run_parsers` runs the parsers and never raises or logs: it returns the document or a
  `Rejection`. The parsing child process (`isolate.py`) calls only this.
- `parse_document` runs them in this process and logs; the worker uses `parse_isolated`
  (`isolate.py`): the same, in a child process with no secrets, a time and a memory limit.
- Spreadsheets (XLSX/CSV) become a cell table (`table.py`) instead of text: they never go
  through `extract_po` (`to_document_input` refuses them) but through
  `app.extraction.spreadsheet.extract_spreadsheet`.
- Logs carry codes, sizes and counts only: never a filename, text, cell, sheet name or
  header value.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import TYPE_CHECKING

import structlog

from app.extraction.inputs.detect import DocumentKind, detect_kind
from app.extraction.inputs.errors import InputError, InputErrorCode, InputWarningCode
from app.extraction.inputs.limits import MAX_FILE_BYTES, MAX_TEXT_CHARS, MAX_TOTAL_IMAGE_BYTES
from app.extraction.inputs.table import SheetTable
from app.llm.types import ImagePart

if TYPE_CHECKING:
    from app.extraction.extract import DocumentInput

__all__ = [
    "DocumentKind",
    "InputError",
    "InputErrorCode",
    "InputWarningCode",
    "ParsedDocument",
    "Rejection",
    "log_outcome",
    "parse_document",
    "run_parsers",
]

log = structlog.get_logger("app.extraction.inputs")


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    kind: DocumentKind
    text: str | None = field(repr=False)  # None = no text layer (image, scanned PDF)
    images: tuple[ImagePart, ...] = field(repr=False)
    page_count: int
    warnings: tuple[InputWarningCode, ...]
    # EML `From` / `Date` as the file claims them: forgeable, for display only (owner,
    # 2026-11-16). Never store them as the received time and never use them to identify a
    # customer or to decide anything (ADR 0012, Follow-up M6-FROM).
    claimed_from: str | None = field(default=None, repr=False)
    claimed_date: datetime | None = None
    # XLSX/CSV only (and always for them): the cell table; `text` is None.
    table: SheetTable | None = field(default=None, repr=False)

    def to_document_input(self) -> "DocumentInput":
        """The model sees the claims only as untrusted metadata inside the document block."""
        if self.table is not None:
            # A spreadsheet's cells never go to `extract_po`: only the column mapping
            # call sees a sample of them (`extract_spreadsheet`).
            raise ValueError("a spreadsheet is extracted with extract_spreadsheet")
        # Imported here: `extract` builds a large character set at import, which the
        # parsing child process doesn't need.
        from app.extraction.extract import DocumentInput

        return DocumentInput(
            text=self.text,
            images=self.images,
            sender_email=self.claimed_from,
            received_at=self.claimed_date,
        )


@dataclass(frozen=True, slots=True)
class Rejection:
    code: InputErrorCode
    kind: DocumentKind | None = None
    error_type: str | None = None  # the exception class behind `invalid_file`, for the log


def _parse(kind: DocumentKind, data: bytes) -> ParsedDocument:
    # Imported here: a child process only loads the parser it needs.
    if kind == "pdf":
        from app.extraction.inputs.pdf import parse_pdf

        pdf = parse_pdf(data)
        return ParsedDocument(
            kind=kind, text=pdf.text, images=pdf.images, page_count=pdf.page_count, warnings=()
        )
    if kind in ("png", "jpeg"):
        from app.extraction.inputs.image import parse_image

        return ParsedDocument(
            kind=kind, text=None, images=(parse_image(data),), page_count=1, warnings=()
        )
    if kind in ("xlsx", "csv"):
        if kind == "xlsx":
            from app.extraction.inputs.xlsx import parse_xlsx

            sheet = parse_xlsx(data)
        else:
            from app.extraction.inputs.csvfile import parse_csv

            sheet = parse_csv(data)
        return ParsedDocument(
            kind=kind,
            text=None,
            images=(),
            page_count=1,
            warnings=sheet.warnings,
            table=sheet.table,
        )
    from app.extraction.inputs.email import parse_email

    email = parse_email(data)
    return ParsedDocument(
        kind=kind,
        text=email.text,
        images=(),
        page_count=1,
        warnings=email.warnings,
        claimed_from=email.claimed_from,
        claimed_date=email.claimed_date,
    )


def clean_text(text: str | None) -> str | None:
    """No NUL (Postgres text can't hold it) and no lone surrogates (not valid UTF-8)."""
    if text is None:
        return None
    cleaned = text.replace("\x00", "").encode("utf-8", "replace").decode("utf-8")
    return cleaned or None


def _check_size(document: ParsedDocument) -> None:
    if document.text is not None and len(document.text) > MAX_TEXT_CHARS:
        raise InputError("document_too_large")
    if sum(len(image.data) for image in document.images) > MAX_TOTAL_IMAGE_BYTES:
        raise InputError("document_too_large")
    if document.text is None and not document.images and document.table is None:
        raise InputError("empty_document")


def run_parsers(data: bytes) -> ParsedDocument | Rejection:
    kind: DocumentKind | None = None
    try:
        if len(data) > MAX_FILE_BYTES:
            raise InputError("file_too_large")
        kind = detect_kind(data)
        parsed = _parse(kind, data)
        document = replace(parsed, text=clean_text(parsed.text))
        _check_size(document)
    except InputError as error:
        return Rejection(code=error.code, kind=kind)
    except MemoryError:
        # The child's memory limit (isolate.py) or a real shortage: not the file's fault.
        return Rejection(code="parse_failed", kind=kind, error_type="MemoryError")
    except Exception as error:
        # A parser exception on bad bytes. Its message can quote the file: only the type
        # is kept.
        return Rejection(code="invalid_file", kind=kind, error_type=type(error).__name__)
    return document


def log_outcome(outcome: ParsedDocument | Rejection, size_bytes: int) -> ParsedDocument:
    """Log the outcome; return the document or raise `InputError` with the rejection's code."""
    if isinstance(outcome, Rejection):
        log.info(
            "document.parse_rejected",
            kind=outcome.kind,
            size_bytes=size_bytes,
            code=outcome.code,
            error_type=outcome.error_type,
        )
        raise InputError(outcome.code)
    log.info(
        "document.parsed",
        kind=outcome.kind,
        size_bytes=size_bytes,
        page_count=outcome.page_count,
        image_count=len(outcome.images),
        image_bytes=sum(len(image.data) for image in outcome.images),
        text_chars=0 if outcome.text is None else len(outcome.text),
        table_rows=0 if outcome.table is None else len(outcome.table.rows),
        table_columns=0 if outcome.table is None else len(outcome.table.columns),
        warnings=list(outcome.warnings),
    )
    return outcome


def parse_document(data: bytes) -> ParsedDocument:
    """In this process. The worker uses `isolate.parse_isolated` instead."""
    return log_outcome(run_parsers(data), len(data))

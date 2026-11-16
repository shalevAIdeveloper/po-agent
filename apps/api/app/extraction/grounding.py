"""Grounding check (Q16): values the model returned must appear in the source text.

Flag only: a value not found in the document's text layer gets an `ungrounded` warning for
the reviewer. Nothing is blanked or changed (product principle 2 is enforced by the
reviewer seeing the flag, never by the code guessing). Runs only for inputs with a text
layer; image-only inputs are skipped by the caller.

Matching errs toward fewer warnings (a false alarm trains reviewers to ignore the flag):
- identifiers (PO number, part numbers): case-insensitive, whitespace removed on both sides
  (PDF text layers split and re-space tokens);
- numbers (qty, prices, totals): by value, after removing `$` and thousands separators, sign
  ignored ("1,234.50" grounds 1234.5; "(5.00)" grounds -5).
"""

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final, Literal

from app.extraction.schema import PurchaseOrderExtraction

_NUMBER_RE: Final = re.compile(r"[0-9][0-9,]*(?:\.[0-9]+)?|\.[0-9]+")
_WHITESPACE_RE: Final = re.compile(r"\s+")

_HEADER_IDS: Final = ("customer_po_number",)
_HEADER_NUMBERS: Final = ("order_total",)
_LINE_IDS: Final = ("customer_part_number", "vendor_part_number")
_LINE_NUMBERS: Final = ("qty", "unit_price", "extended_price")


@dataclass(frozen=True, slots=True)
class ExtractionWarning:
    """A flag for the reviewer: a code and a field path, never a value."""

    code: Literal["ungrounded"]
    path: str


def _fold(text: str) -> str:
    return _WHITESPACE_RE.sub("", unicodedata.normalize("NFKC", text)).casefold()


def _numbers(text: str) -> set[Decimal]:
    found: set[Decimal] = set()
    for token in _NUMBER_RE.findall(unicodedata.normalize("NFKC", text)):
        try:
            found.add(abs(Decimal(token.replace(",", ""))))
        except InvalidOperation:  # e.g. a lone "," run is impossible, but stay total
            continue
    return found


def check_grounding(
    extraction: PurchaseOrderExtraction, source_text: str
) -> tuple[ExtractionWarning, ...]:
    folded = _fold(source_text)
    numbers = _numbers(source_text)
    warnings: list[ExtractionWarning] = []

    def check_id(value: str | None, path: str) -> None:
        if value is not None and _fold(value) not in folded:
            warnings.append(ExtractionWarning(code="ungrounded", path=path))

    def check_number(value: Decimal | None, path: str) -> None:
        if value is not None and abs(value) not in numbers:
            warnings.append(ExtractionWarning(code="ungrounded", path=path))

    header = extraction.header
    for field in _HEADER_IDS:
        check_id(getattr(header, field), f"header.{field}")
    for field in _HEADER_NUMBERS:
        check_number(getattr(header, field), f"header.{field}")
    for index, line in enumerate(extraction.lines):
        for field in _LINE_IDS:
            check_id(getattr(line, field), f"lines.{index}.{field}")
        for field in _LINE_NUMBERS:
            check_number(getattr(line, field), f"lines.{index}.{field}")
    return tuple(warnings)

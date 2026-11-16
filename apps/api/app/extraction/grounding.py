"""Grounding check (Q16): values the model returned must appear in the source text.

Flag only: a value not found in the document's text layer gets an `ungrounded` warning for
the reviewer. Nothing is blanked or changed (product principle 2 is enforced by the
reviewer seeing the flag, never by the code guessing). Runs only for inputs with a text
layer; image-only inputs are skipped by the caller.

Matching errs toward fewer warnings (a false alarm trains reviewers to ignore the flag):
- identifiers (PO number, part numbers): case-insensitive, whitespace removed on both sides
  (PDF text layers split and re-space tokens);
- short identifiers (1 or 2 characters after that folding, e.g. "A", "9", "A1") must be a whole
  token: whitespace (or the start/end of the text) on both sides, optionally with
  punctuation in between ("(A)", "#A1.", "A 1"). As a substring they'd match almost any
  document ("A" in "ACME"). A short id joined to letters, digits or `_` ("00123-A" for "A"
  is joined by "00123") is flagged;
- numbers (qty, prices, totals): by value, after removing `$` and thousands separators, sign
  ignored ("1,234.50" grounds 1234.5; "(5.00)" grounds -5); more than 40 digits never
  matches.

Grounding catches values the model made up. It does not catch injection: a value an
attacker printed in the document is, by definition, in the document.
"""

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final, Literal

from app.extraction.schema import PurchaseOrderExtraction

_NUMBER_RE: Final = re.compile(r"[0-9][0-9,]*(?:\.[0-9]+)?|\.[0-9]+")
_WHITESPACE_RE: Final = re.compile(r"\s+")
# No quantity or price has more digits; longer runs (IDs, junk) are never matched.
MAX_DIGITS: Final = 40
# Identifiers up to this many characters (folded) are matched as a whole token.
SHORT_ID_MAX: Final = 2
# Around a short id: whitespace or the text's edge, then only punctuation.
_SHORT_ID_BEFORE: Final = r"(?<!\S)[^\w\s]*"
_SHORT_ID_AFTER: Final = r"[^\w\s]*(?!\S)"

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


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _short_id_pattern(folded_value: str) -> re.Pattern[str]:
    # At most two escaped characters with one `\s*` between them: linear on any input.
    core = r"\s*".join(re.escape(char) for char in folded_value)
    return re.compile(_SHORT_ID_BEFORE + core + _SHORT_ID_AFTER)


def _too_long(value: Decimal) -> bool:
    return len(value.as_tuple().digits) > MAX_DIGITS


def _numbers(text: str) -> set[Decimal]:
    found: set[Decimal] = set()
    for token in _NUMBER_RE.findall(unicodedata.normalize("NFKC", text)):
        try:
            # Exact: Decimal(str) and copy_abs() use no context, so no rounding or
            # Overflow on a huge token (`sec` M1); abs() would round and could raise.
            number = Decimal(token.replace(",", "")).copy_abs()
        except InvalidOperation:
            continue
        if not _too_long(number):
            found.add(number)
    return found


def check_grounding(
    extraction: PurchaseOrderExtraction, source_text: str
) -> tuple[ExtractionWarning, ...]:
    folded = _fold(source_text)
    normalized = _normalize(source_text)
    numbers = _numbers(source_text)
    warnings: list[ExtractionWarning] = []

    def grounded_id(value: str) -> bool:
        folded_value = _fold(value)
        if 0 < len(folded_value) <= SHORT_ID_MAX:
            return _short_id_pattern(folded_value).search(normalized) is not None
        return folded_value in folded

    def check_id(value: str | None, path: str) -> None:
        if value is not None and not grounded_id(value):
            warnings.append(ExtractionWarning(code="ungrounded", path=path))

    def check_number(value: Decimal | None, path: str) -> None:
        if value is not None and (_too_long(value) or value.copy_abs() not in numbers):
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

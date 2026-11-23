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
  is joined by "00123") is flagged. At most 8 punctuation characters on each side, and
  only spaces/tabs between the two characters (not a line break), keep the match linear
  on hostile text (`sec` H1). A short id with no letter or digit ("-", "#") is always
  flagged: standalone punctuation is in almost every document (`sec` L2). This is still a
  weak check: a common token ("A" as the English article, "1" as a line number) grounds
  an invented 1-character id, so "grounded" means "printed somewhere", not "printed in
  this field";
- numbers (qty, prices, totals): by value, after removing `$` and thousands separators, sign
  ignored ("1,234.50" grounds 1234.5; "(5.00)" grounds -5); more than 40 digits never
  matches.

Grounding catches values the model made up. It does not catch injection: a value an
attacker printed in the document is, by definition, in the document.
"""

import dataclasses
import itertools
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final, Literal

from app.extraction.inputs.limits import (
    MAX_ROWS_READ,
    MAX_SHEET_COLUMN,
    MAX_SHEET_NAMES,
    MAX_TABLE_CHARS,
    MAX_TABLE_COLUMNS,
    MAX_TABLE_ROWS,
)
from app.extraction.inputs.table import clean_cell, clean_sheet_name
from app.extraction.schema import PurchaseOrderExtraction

_NUMBER_RE: Final = re.compile(r"[0-9][0-9,]*(?:\.[0-9]+)?|\.[0-9]+")
_WHITESPACE_RE: Final = re.compile(r"\s+")
# No quantity or price has more digits; longer runs (IDs, junk) are never matched.
MAX_DIGITS: Final = 40
# Identifiers up to this many characters (folded) are matched as a whole token.
SHORT_ID_MAX: Final = 2
# Around a short id: whitespace or the text's edge, then a little punctuation. Bounded runs:
# with `*` on both sides, a punctuation id over a long punctuation run was quadratic (`sec` H1).
MAX_SHORT_ID_PUNCTUATION: Final = 8
_SHORT_ID_BEFORE: Final = rf"(?<!\S)[^\w\s]{{0,{MAX_SHORT_ID_PUNCTUATION}}}"
_SHORT_ID_AFTER: Final = rf"[^\w\s]{{0,{MAX_SHORT_ID_PUNCTUATION}}}(?!\S)"
_WORD_CHAR_RE: Final = re.compile(r"\w")

_HEADER_IDS: Final = ("customer_po_number",)
_HEADER_NUMBERS: Final = ("order_total",)
_LINE_IDS: Final = ("customer_part_number", "vendor_part_number")
_LINE_NUMBERS: Final = ("qty", "unit_price", "extended_price")


WarningCode = Literal[
    # Q16: not found in the source text.
    "ungrounded",
    # Spreadsheets (M1 PR 5): the cell couldn't be read as the field's type (null,
    # raw_text kept); a negative qty / price / total, kept, shown to the reviewer.
    "unparsed_value",
    "negative_value",
    # Spreadsheets, with details (owner, approved plan item (a)): other visible sheets not
    # read (their names), hidden rows / columns dropped (counts), uncached formulas (count).
    "other_sheets_ignored",
    "hidden_cells_ignored",
    "formula_without_value",
    # Rows with data that are not lines (above first_data_row, below the heading row, or
    # footer rows): row numbers and their text, so nothing disappears silently (owner, M4).
    "rows_skipped",
]

# Bounds on details (they are document content: shown to the reviewer, never logged).
MAX_DETAIL_ROWS: Final = MAX_TABLE_ROWS
# A skipped row's text is its non-empty cells joined with " | " (like raw_text).
MAX_DETAIL_TEXT_CHARS: Final = MAX_TABLE_CHARS + 3 * MAX_TABLE_ROWS * MAX_TABLE_COLUMNS
_MAX_COUNT: Final = MAX_ROWS_READ * MAX_SHEET_COLUMN


def _count(value: int | None) -> None:
    if value is not None and (type(value) is not int or not 0 <= value <= _MAX_COUNT):
        raise ValueError("a count must be a non-negative int within the read caps")


@dataclass(frozen=True, slots=True)
class WarningDetails:
    """What a warning is about. Typed and bounded; checked on construction."""

    names: tuple[str, ...] = dataclasses.field(default=(), repr=False)  # sheet names
    rows: tuple[int, ...] = ()  # source row numbers, increasing
    texts: tuple[str, ...] = dataclasses.field(
        default=(), repr=False
    )  # one per row: its cells joined
    hidden_rows: int | None = None
    hidden_columns: int | None = None
    count: int | None = None

    def __post_init__(self) -> None:
        if len(self.names) > MAX_SHEET_NAMES:
            raise ValueError("too many sheet names")
        for name in self.names:
            if not isinstance(name, str) or clean_sheet_name(name) != name:
                raise ValueError("a sheet name must be cleaned and at most 31 characters")
        if len(self.rows) > MAX_DETAIL_ROWS or len(self.texts) != len(self.rows):
            raise ValueError("at most MAX_DETAIL_ROWS rows, one text each")
        if any(type(row) is not int or not 1 <= row <= MAX_ROWS_READ for row in self.rows):
            raise ValueError("a row number must be an int within the rows read")
        if any(a >= b for a, b in itertools.pairwise(self.rows)):
            raise ValueError("row numbers must increase")
        if sum(len(text) for text in self.texts) > MAX_DETAIL_TEXT_CHARS:
            raise ValueError("row texts over the cap")
        for text in self.texts:
            if not isinstance(text, str) or clean_cell(text) != text:
                raise ValueError("a row text must be cleaned cell text")
        for value in (self.hidden_rows, self.hidden_columns, self.count):
            _count(value)


@dataclass(frozen=True, slots=True)
class ExtractionWarning:
    """A flag for the reviewer: a code and a field path. Values only in `details`, and only
    for spreadsheet warnings (never for PDF/image/email or grounding warnings)."""

    code: WarningCode
    path: str
    details: WarningDetails | None = None


def _fold(text: str) -> str:
    return _WHITESPACE_RE.sub("", unicodedata.normalize("NFKC", text)).casefold()


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _short_id_pattern(folded_value: str) -> re.Pattern[str]:
    # At most two escaped characters, spaces/tabs between them (never a line break: `sec` L1).
    core = r"[ \t]*".join(re.escape(char) for char in folded_value)
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
            if _WORD_CHAR_RE.search(folded_value) is None:
                return False
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

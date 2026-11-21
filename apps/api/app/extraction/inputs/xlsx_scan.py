"""A streaming pass over one worksheet's XML before openpyxl reads it (owner, 2026-11-16).

openpyxl's read-only mode doesn't expose row or column visibility, and with `data_only`
an uncached formula looks like an empty cell. This pass (defusedxml `iterparse`, DTDs
forbidden, elements cleared as it goes) finds:
- hidden rows (`hidden`, a height under MIN_VISIBLE_SIZE; autofilter-hidden rows carry
  `hidden="1"`; with `<sheetFormatPr zeroHeight>` every row without a custom height;
  with a zero default height every row without its own) and hidden columns (`hidden`, a
  width under MIN_VISIBLE_SIZE; with a zero default width every column no `<col>` covers):
  all of them are dropped before the table is built, data or not, so they never reach the
  wire, the model or `raw_text`; the warning counts those holding data (`sec` M3, L1, L2);
- formula cells (`<f>`) with no cached value (`<v>`): left empty, and counted;
- the last row and column holding visible data, which bound openpyxl's read.

It follows openpyxl's numbering (a `<row>` without `r` is the previous + 1, a `<c>`
without `r` the previous column + 1), and refuses rows or cells out of order or beyond
Excel's grid (`invalid_file`): openpyxl would silently drop or overwrite them.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import IO, Final
from xml.etree.ElementTree import Element

import defusedxml.ElementTree as SafeET

from app.extraction.inputs.errors import InputError
from app.extraction.inputs.limits import MAX_ROWS_READ, MAX_SHEET_COLUMN, MAX_SHEET_ROW

_NS: Final = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_SHEET_DATA: Final = f"{_NS}sheetData"
_ROW: Final = f"{_NS}row"
_CELL: Final = f"{_NS}c"
_COL: Final = f"{_NS}col"
_VALUE: Final = f"{_NS}v"
_FORMULA: Final = f"{_NS}f"
_INLINE: Final = f"{_NS}is"
_SHEET_FORMAT: Final = f"{_NS}sheetFormatPr"
_REF_RE: Final = re.compile(r"\$?([A-Z]{1,3})\$?([0-9]{1,7})")
_DIGITS_RE: Final = re.compile(r"[0-9]{1,7}")
_TRUE: Final = frozenset({"1", "true"})
# A row height or column width below this (points / characters) shows nothing (`sec` L1).
MIN_VISIBLE_SIZE: Final = 0.5


@dataclass(frozen=True, slots=True)
class SheetScan:
    has_content: bool  # data in a visible row and column
    last_row: int  # last visible row holding data (0 if none)
    last_column: int  # last visible column holding data (0 if none)
    # Rows / columns to drop: every hidden one, data or not (`sec` L2); rows only up to
    # MAX_ROWS_READ (nothing below is read).
    hidden_rows: frozenset[int] = field(repr=False)
    hidden_columns: frozenset[int] = field(repr=False)
    hidden_row_count: int  # hidden rows holding data (for the warning)
    hidden_column_count: int  # hidden columns holding data (for the warning)
    formulas_without_value: int  # in visible rows and columns


def _column_number(letters: str) -> int:
    number = 0
    for letter in letters:
        number = number * 26 + ord(letter) - ord("A") + 1
    return number


def _is_true(value: str | None) -> bool:
    """xsd:boolean as Excel reads it: surrounding whitespace allowed (`sec` L1)."""
    return value is not None and value.strip().lower() in _TRUE


def _is_tiny(value: str | None) -> bool:
    """A size that shows nothing: below MIN_VISIBLE_SIZE (or not a finite number)."""
    if value is None:
        return False
    try:
        size = float(value)
    except ValueError:
        return False
    return not size >= MIN_VISIBLE_SIZE  # NaN is tiny too


def _has_value(cell: Element) -> bool:
    """openpyxl's rule: a non-empty `<v>`, or an inline string with text."""
    if cell.get("t") == "inlineStr":
        inline = cell.find(_INLINE)
        return inline is not None and bool("".join(inline.itertext()))
    return bool(cell.findtext(_VALUE))


@dataclass(slots=True)
class _Format:
    """`<sheetFormatPr>`: what rows and columns without their own size look like (`sec` M3)."""

    zero_height: bool = False  # rows are hidden unless they set a custom, visible height
    default_row_tiny: bool = False
    default_column_tiny: bool = False

    def row_hidden(self, row: Element) -> bool:
        if _is_true(row.get("hidden")):
            return True
        height = row.get("ht")
        if height is not None and _is_tiny(height):
            return True
        if self.zero_height:
            return not (_is_true(row.get("customHeight")) and height is not None)
        return height is None and self.default_row_tiny


class _Columns:
    """Hidden columns: `<col hidden>` / a tiny width, and with a tiny default width, every
    column no `<col>` covers."""

    def __init__(self) -> None:
        self.hidden = bytearray(MAX_SHEET_COLUMN + 1)
        self.covered = bytearray(MAX_SHEET_COLUMN + 1)
        self.default_tiny = False

    def add(self, col: Element) -> None:
        low, high = col.get("min"), col.get("max")
        if (
            low is None
            or high is None
            or not (_DIGITS_RE.fullmatch(low) and _DIGITS_RE.fullmatch(high))
        ):
            raise InputError("invalid_file")
        first, last = int(low), min(int(high), MAX_SHEET_COLUMN)
        if not 1 <= first <= last:
            raise InputError("invalid_file")
        span = last - first + 1
        self.covered[first : last + 1] = b"\x01" * span
        if _is_true(col.get("hidden")) or _is_tiny(col.get("width")):
            self.hidden[first : last + 1] = b"\x01" * span

    def is_hidden(self, column: int) -> bool:
        return bool(self.hidden[column]) or (self.default_tiny and not self.covered[column])

    def all_hidden(self) -> frozenset[int]:
        return frozenset(c for c in range(1, MAX_SHEET_COLUMN + 1) if self.is_hidden(c))


def scan_sheet(source: IO[bytes], *, until_content: bool = False) -> SheetScan:
    """`until_content`: stop at the first visible data (other sheets: is there any?)."""
    fmt = _Format()
    columns = _Columns()
    data_columns: set[int] = set()  # columns with data, any row
    visible_row_columns: set[int] = set()  # columns with data in visible rows
    hidden_rows: set[int] = set()
    hidden_row_count = 0
    formula_columns: Counter[int] = Counter()
    last_row = 0
    row_number = 0
    column = 0
    row_hidden = False
    row_has_data = False
    sheet_data = None
    # Open elements, outermost first. A finished element is cleared and detached from its
    # parent unless it is inside a cell (the cell reads its <v>/<f>/<is> at its end), so
    # memory stays flat everywhere: rows, mergeCells, conditionalFormatting, extLst, ...
    # (`sec` L5). Siblings finish one at a time, so a parent holds at most one child.
    open_elements: list[Element] = []
    open_cells = 0
    for event, element in SafeET.iterparse(source, events=("start", "end"), forbid_dtd=True):
        tag = element.tag
        if event == "start":
            open_elements.append(element)
            if tag == _CELL:
                open_cells += 1
            if tag == _ROW:
                ref = element.get("r")
                if ref is None:
                    number = row_number + 1
                elif _DIGITS_RE.fullmatch(ref):
                    number = int(ref)
                else:
                    raise InputError("invalid_file")
                if not row_number < number <= MAX_SHEET_ROW:
                    raise InputError("invalid_file")
                row_number, column = number, 0
                row_hidden = fmt.row_hidden(element)
                row_has_data = False
                if row_hidden and row_number <= MAX_ROWS_READ:
                    hidden_rows.add(row_number)
            elif tag == _SHEET_DATA:
                sheet_data = element
            elif tag == _SHEET_FORMAT:
                if sheet_data is not None:
                    raise InputError("invalid_file")  # it must come before the rows
                fmt.zero_height = _is_true(element.get("zeroHeight"))
                fmt.default_row_tiny = _is_tiny(element.get("defaultRowHeight"))
                columns.default_tiny = _is_tiny(element.get("defaultColWidth"))
            continue
        open_elements.pop()
        if tag == _CELL:
            open_cells -= 1
            ref = element.get("r")
            if ref is None:
                number = column + 1
            else:
                match = _REF_RE.fullmatch(ref)
                if match is None:
                    raise InputError("invalid_file")
                number = _column_number(match.group(1))
            if not column < number <= MAX_SHEET_COLUMN:
                raise InputError("invalid_file")
            column = number
            if _has_value(element):
                row_has_data = True
                data_columns.add(column)
                if not row_hidden:
                    visible_row_columns.add(column)
                    if until_content and not columns.is_hidden(column):
                        element.clear()
                        return _found(row_number, column)
            elif element.find(_FORMULA) is not None and not row_hidden:
                formula_columns[column] += 1
        elif tag == _ROW:
            if row_has_data:
                if not row_hidden:
                    last_row = row_number
                else:
                    hidden_row_count += 1
        elif tag == _COL:
            columns.add(element)
        if open_cells == 0:
            element.clear()
            if open_elements:
                open_elements[-1].remove(element)
    hidden_columns = columns.all_hidden()
    visible = visible_row_columns - hidden_columns
    return SheetScan(
        has_content=bool(visible),
        last_row=last_row if visible else 0,
        last_column=max(visible, default=0),
        hidden_rows=frozenset(hidden_rows),
        hidden_columns=hidden_columns,
        hidden_row_count=hidden_row_count,
        hidden_column_count=len(data_columns & hidden_columns),
        formulas_without_value=sum(
            count for c, count in formula_columns.items() if c not in hidden_columns
        ),
    )


def _found(row: int, column: int) -> SheetScan:
    """until_content: only "is there visible data" matters."""
    return SheetScan(
        has_content=True,
        last_row=row,
        last_column=column,
        hidden_rows=frozenset(),
        hidden_columns=frozenset(),
        hidden_row_count=0,
        hidden_column_count=0,
        formulas_without_value=0,
    )

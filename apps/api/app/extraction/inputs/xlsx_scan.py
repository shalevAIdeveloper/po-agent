"""A streaming pass over one worksheet's XML before openpyxl reads it (owner, 2026-11-16).

openpyxl's read-only mode doesn't expose row or column visibility, and with `data_only`
an uncached formula looks like an empty cell. This pass (defusedxml `iterparse`, DTDs
forbidden, elements cleared as it goes) finds:
- hidden rows (`hidden="1"` or `ht="0"`; autofilter-hidden rows carry `hidden="1"`) and
  hidden columns (`<col hidden="1">` or `width="0"`) that hold data: they are dropped
  before the table is built, so they never reach the wire, the model or `raw_text`;
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
_REF_RE: Final = re.compile(r"\$?([A-Z]{1,3})\$?([0-9]{1,7})")
_DIGITS_RE: Final = re.compile(r"[0-9]{1,7}")
_TRUE: Final = frozenset({"1", "true"})


@dataclass(frozen=True, slots=True)
class SheetScan:
    has_content: bool  # data in a visible row and column
    last_row: int  # last visible row holding data (0 if none)
    last_column: int  # last visible column holding data (0 if none)
    # Rows / columns to drop: hidden and holding data (rows only up to MAX_ROWS_READ).
    hidden_rows: frozenset[int] = field(repr=False)
    hidden_columns: frozenset[int] = field(repr=False)
    hidden_row_count: int  # hidden rows holding data
    hidden_column_count: int  # hidden columns holding data
    formulas_without_value: int  # in visible rows and columns


def _column_number(letters: str) -> int:
    number = 0
    for letter in letters:
        number = number * 26 + ord(letter) - ord("A") + 1
    return number


def _is_zero(value: str | None) -> bool:
    if value is None:
        return False
    try:
        return float(value) == 0
    except ValueError:
        return False


def _has_value(cell: Element) -> bool:
    """openpyxl's rule: a non-empty `<v>`, or an inline string with text."""
    if cell.get("t") == "inlineStr":
        inline = cell.find(_INLINE)
        return inline is not None and bool("".join(inline.itertext()))
    return bool(cell.findtext(_VALUE))


def scan_sheet(source: IO[bytes], *, until_content: bool = False) -> SheetScan:
    """`until_content`: stop at the first visible data (other sheets: is there any?)."""
    hidden_mask = bytearray(MAX_SHEET_COLUMN + 1)
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
    for event, element in SafeET.iterparse(source, events=("start", "end"), forbid_dtd=True):
        tag = element.tag
        if event == "start":
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
                row_hidden = element.get("hidden") in _TRUE or _is_zero(element.get("ht"))
                row_has_data = False
            elif tag == _SHEET_DATA:
                sheet_data = element
            continue
        if tag == _CELL:
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
                    if until_content and not hidden_mask[column]:
                        element.clear()
                        return _result(True, row_number, column, hidden_mask, set(), set(), 0, {})
            elif element.find(_FORMULA) is not None and not row_hidden:
                formula_columns[column] += 1
            element.clear()
        elif tag == _ROW:
            if row_has_data:
                if not row_hidden:
                    last_row = row_number
                else:
                    hidden_row_count += 1
                    if row_number <= MAX_ROWS_READ:
                        hidden_rows.add(row_number)
            element.clear()
            if sheet_data is not None:
                sheet_data.clear()  # processed rows: memory stays flat
        elif tag == _COL:
            low, high = element.get("min"), element.get("max")
            if (
                low is None
                or high is None
                or not (_DIGITS_RE.fullmatch(low) and _DIGITS_RE.fullmatch(high))
            ):
                raise InputError("invalid_file")
            first, last = int(low), min(int(high), MAX_SHEET_COLUMN)
            if not 1 <= first <= last:
                raise InputError("invalid_file")
            if element.get("hidden") in _TRUE or _is_zero(element.get("width")):
                hidden_mask[first : last + 1] = b"\x01" * (last - first + 1)
    return _result(
        False,
        last_row,
        0,
        hidden_mask,
        data_columns,
        visible_row_columns,
        hidden_row_count,
        formula_columns,
        hidden_rows=hidden_rows,
    )


def _result(
    found: bool,
    last_row: int,
    found_column: int,
    hidden_mask: bytearray,
    data_columns: set[int],
    visible_row_columns: set[int],
    hidden_row_count: int,
    formula_columns: dict[int, int],
    *,
    hidden_rows: set[int] | None = None,
) -> SheetScan:
    if found:  # until_content: only "is there visible data" matters
        return SheetScan(
            has_content=True,
            last_row=last_row,
            last_column=found_column,
            hidden_rows=frozenset(),
            hidden_columns=frozenset(),
            hidden_row_count=0,
            hidden_column_count=0,
            formulas_without_value=0,
        )
    hidden_columns = frozenset(c for c in data_columns if hidden_mask[c])
    visible = visible_row_columns - hidden_columns
    return SheetScan(
        has_content=bool(visible),
        last_row=last_row if visible else 0,
        last_column=max(visible, default=0),
        hidden_rows=frozenset(hidden_rows or ()),
        hidden_columns=hidden_columns,
        hidden_row_count=hidden_row_count,
        hidden_column_count=len(hidden_columns),
        formulas_without_value=sum(
            count for c, count in formula_columns.items() if not hidden_mask[c]
        ),
    )

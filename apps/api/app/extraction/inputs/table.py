"""The cell table a spreadsheet becomes (XLSX first sheet or CSV), built in the child.

- Only non-empty rows are kept, each with its row number in the source (the XLSX row,
  the CSV record), strictly increasing.
- Only columns that hold data are kept, each with its column number in the source
  (1 = A), strictly increasing; every row has one cell per kept column.
- A cell is text (as rendered by the parser) or None. Text is cleaned for storage and for
  the prompt: line breaks become LF, NUL and other control characters (but tab and LF)
  and format characters (Unicode Cf: bidi, zero-width) are removed, lone surrogates are
  replaced. Whitespace-only text is None.
- Caps (limits.py): MAX_TABLE_ROWS non-empty rows (`too_many_rows`), MAX_TABLE_COLUMNS
  columns, MAX_CELL_CHARS per cell and MAX_TABLE_CHARS per table (`document_too_large`).

Cells, sheet names and values are document content: never logged, never in a repr.
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Final

from app.extraction.inputs.errors import InputError, InputWarningCode
from app.extraction.inputs.limits import (
    MAX_CELL_CHARS,
    MAX_SHEET_COLUMN,
    MAX_SHEET_NAME_CHARS,
    MAX_TABLE_CHARS,
    MAX_TABLE_COLUMNS,
    MAX_TABLE_ROWS,
)

_CELL_CONTROL_RE: Final = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_NAME_CONTROL_RE: Final = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _without_format_characters(text: str) -> str:
    """Bidi overrides, zero-width characters, BOM, soft hyphen (Unicode Cf) removed: they
    can reorder or hide text for the reviewer and split a part number (`sec` L3)."""
    if text.isascii():
        return text
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def clean_cell(text: str) -> str | None:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CELL_CONTROL_RE.sub("", _without_format_characters(text))
    text = text.encode("utf-8", "replace").decode("utf-8")
    return text if text.strip() else None


def clean_sheet_name(name: str) -> str | None:
    """One line, at most MAX_SHEET_NAME_CHARS; None if blank.

    Like cell text, plus: control characters become spaces and format characters (bidi
    overrides, zero-width) are removed, since the name is shown as a label in a warning.
    """
    name = _without_format_characters(name)
    flat = _NAME_CONTROL_RE.sub(" ", name).encode("utf-8", "replace").decode("utf-8")
    flat = " ".join(flat.split())[:MAX_SHEET_NAME_CHARS].strip()
    return flat or None


@dataclass(frozen=True, slots=True)
class TableRow:
    number: int  # row in the source: XLSX row number, CSV record number (1-based)
    cells: tuple[str | None, ...] = field(repr=False)  # one per `SheetTable.columns`


@dataclass(frozen=True, slots=True)
class SheetTable:
    columns: tuple[int, ...]  # source column numbers (1 = A), strictly increasing
    rows: tuple[TableRow, ...] = field(repr=False)
    # XLSX: names of the other visible sheets with data, not read (owner, 2026-11-16).
    # Document content: shown to the reviewer, never logged, never sent to the model.
    other_sheets: tuple[str, ...] = field(default=(), repr=False)
    hidden_rows: int = 0  # hidden rows holding data, dropped
    hidden_columns: int = 0  # hidden columns holding data, dropped
    formulas_without_value: int = 0  # formula cells with no cached value, left empty

    @property
    def char_count(self) -> int:
        return sum(len(cell) for row in self.rows for cell in row.cells if cell is not None)


@dataclass(frozen=True, slots=True)
class SpreadsheetParse:
    table: SheetTable
    warnings: tuple[InputWarningCode, ...]


class TableBuilder:
    """Collects non-empty rows and enforces the table caps as it goes."""

    def __init__(self) -> None:
        self._rows: list[tuple[int, dict[int, str]]] = []
        self._columns: set[int] = set()
        self._chars = 0

    def add_row(self, number: int, cells: Iterable[tuple[int, str | None]]) -> None:
        """`cells` are (column number, text) pairs in increasing column order."""
        row: dict[int, str] = {}
        for column, raw in cells:
            if not raw:
                continue
            text = clean_cell(raw)
            if text is None:
                continue
            if len(text) > MAX_CELL_CHARS or column > MAX_SHEET_COLUMN:
                raise InputError("document_too_large")
            row[column] = text
            self._columns.add(column)
            if len(self._columns) > MAX_TABLE_COLUMNS:
                raise InputError("document_too_large")
        if not row:
            return
        if self._rows and number <= self._rows[-1][0]:
            raise InputError("invalid_file")
        if len(self._rows) >= MAX_TABLE_ROWS:
            raise InputError("too_many_rows")
        self._chars += sum(len(text) for text in row.values())
        if self._chars > MAX_TABLE_CHARS:
            raise InputError("document_too_large")
        self._rows.append((number, row))

    def build(
        self,
        *,
        other_sheets: tuple[str, ...] = (),
        hidden_rows: int = 0,
        hidden_columns: int = 0,
        formulas_without_value: int = 0,
    ) -> SheetTable:
        if not self._rows:
            raise InputError("empty_document")
        columns = tuple(sorted(self._columns))
        return SheetTable(
            columns=columns,
            rows=tuple(
                TableRow(number=number, cells=tuple(row.get(column) for column in columns))
                for number, row in self._rows
            ),
            other_sheets=other_sheets,
            hidden_rows=hidden_rows,
            hidden_columns=hidden_columns,
            formulas_without_value=formulas_without_value,
        )

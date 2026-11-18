"""XLSX / XLSM: the first visible sheet with data becomes a `SheetTable` (M1 PR 5).

- The ZIP central directory passes the caps first (`archive.py`), and the shared-strings
  part must be under MAX_SHARED_STRINGS_BYTES.
- openpyxl in read-only mode, `data_only` (cached values only: a formula is never
  evaluated and its text never read), `keep_links=False` (external workbooks never
  read), `keep_vba=False` (macros never read or run; `macros_ignored`). Chart sheets
  are hidden from openpyxl, so their drawings and images are never parsed.
- **openpyxl must parse XML with defusedxml and never lxml**: checked at import, and the
  parser refuses to run otherwise (`parse_failed`, fail closed). Our own XML reads
  (content types, the pre-pass) use defusedxml directly, and before openpyxl runs
  every XML part's prolog is checked: a DTD anywhere is `invalid_file`.
- Each sheet's XML is pre-scanned (`xlsx_scan.py`): hidden rows and columns holding
  data are dropped (`hidden_cells_ignored`), uncached formulas are counted
  (`formula_without_value`), and openpyxl's read is bounded by the last visible data
  row and column. Data below MAX_ROWS_READ is `too_many_rows`.
- Hidden sheets are never read (`hidden_sheets_ignored` when one holds data). Other
  visible sheets with data are not read either (`other_sheets_ignored`); their names,
  cleaned and capped, go into the table for the reviewer (never logged, never sent to
  the model).
- Cells are rendered here, in the child: integers as digits (zero-padded when the
  number format is all zeros, e.g. `00123`), other numbers with at most 15 significant
  digits and no exponent, dates as ISO `YYYY-MM-DD` (with `THH:MM:SS` if there is a
  time), booleans as TRUE/FALSE, error values (`#N/A`) as written.
- No thread is started (the child runs under RLIMIT_NPROC 0).
"""

import io
import math
import re
import warnings
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Final

import openpyxl.xml
import openpyxl.xml.functions

from app.extraction.inputs.archive import open_archive, read_content_types, refuse_dtds
from app.extraction.inputs.detect import WORKBOOK_PART, XLSM_MAIN
from app.extraction.inputs.errors import InputError, InputWarningCode
from app.extraction.inputs.limits import (
    MAX_ROWS_READ,
    MAX_SHARED_STRINGS_BYTES,
    MAX_SHEET_NAMES,
)
from app.extraction.inputs.table import (
    SpreadsheetParse,
    TableBuilder,
    clean_sheet_name,
)
from app.extraction.inputs.xlsx_scan import SheetScan, scan_sheet


def xml_is_hardened() -> bool:
    """openpyxl parses with defusedxml (both entry points it uses) and lxml is off."""
    functions = openpyxl.xml.functions
    return (
        openpyxl.xml.DEFUSEDXML is True
        and openpyxl.xml.LXML is False
        and functions.iterparse.__module__.startswith("defusedxml")
        and functions.fromstring.__module__.startswith("defusedxml")
    )


if not xml_is_hardened():
    # Fail closed: the child reports `parse_failed`, never a workbook parsed unsafely.
    raise InputError("parse_failed")

from openpyxl.reader.excel import ExcelReader  # noqa: E402 - only after the check

SHARED_STRINGS_TYPE: Final = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"
)
_CHARTSHEETS: Final = "xl/chartsheets/"
_ZERO_PAD_RE: Final = re.compile(r"0+")
_SIGNIFICANT_DIGITS: Final = 15


def _number(value: float) -> str | None:
    if not math.isfinite(value):
        return None
    if value == 0:
        return "0"
    return format(Decimal(f"{value:.{_SIGNIFICANT_DIGITS}g}"), "f")


def render_cell(value: Any, number_format: str | None) -> str | None:
    """A cell value as text, never the formula. None for an empty cell."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    pad = (
        len(number_format)
        if number_format is not None and _ZERO_PAD_RE.fullmatch(number_format)
        else 0
    )
    if isinstance(value, int):
        return f"{value:0{pad}d}" if pad and value >= 0 else str(value)
    if isinstance(value, float):
        if pad and value >= 0 and value.is_integer():
            return f"{int(value):0{pad}d}"
        return _number(value)
    if isinstance(value, datetime):
        if value.time() == time(0):
            return value.date().isoformat()
        return value.isoformat(timespec="seconds")
    if isinstance(value, date | time):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, str):
        return value
    return None  # nothing else is a cached value (openpyxl objects for formulas)


def _scan(package: Any, path: object, *, until_content: bool) -> SheetScan:
    if not isinstance(path, str):
        raise InputError("invalid_file")
    with package.open(path) as source:
        return scan_sheet(source, until_content=until_content)


def _check_shared_strings(package: Any) -> bool:
    """Whether the workbook has macros; refuses shared strings over the cap."""
    types = read_content_types(package)
    for part in types.parts_of_type(SHARED_STRINGS_TYPE):
        if package.getinfo(part.lstrip("/")).file_size > MAX_SHARED_STRINGS_BYTES:
            raise InputError("document_too_large")
    return types.of(WORKBOOK_PART) == XLSM_MAIN or any(
        name.lower().endswith("vbaproject.bin") for name in package.namelist()
    )


def parse_xlsx(data: bytes) -> SpreadsheetParse:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # openpyxl warns about unsupported extensions
        with open_archive(data) as package:
            macros = _check_shared_strings(package)
            refuse_dtds(package)
            reader = ExcelReader(
                io.BytesIO(data),
                read_only=True,
                keep_vba=False,
                data_only=True,
                keep_links=False,
            )
            reader.valid_files = [n for n in reader.valid_files if not n.startswith(_CHARTSHEETS)]
            reader.read()
            workbook = reader.wb
            try:
                return _read(workbook, package, macros=macros)
            finally:
                workbook.close()


def _read(workbook: Any, package: Any, *, macros: bool) -> SpreadsheetParse:
    chosen: Any = None
    scan: SheetScan | None = None
    other_sheets: list[str] = []
    others = 0
    hidden_with_data = False
    for sheet in workbook.worksheets:
        path = getattr(sheet, "_worksheet_path", None)
        if sheet.sheet_state != "visible":
            hidden_with_data = (
                hidden_with_data or _scan(package, path, until_content=True).has_content
            )
        elif chosen is None:
            candidate = _scan(package, path, until_content=False)
            if candidate.has_content:
                chosen, scan = sheet, candidate
        elif _scan(package, path, until_content=True).has_content:
            others += 1
            name = clean_sheet_name(str(sheet.title))
            if name is not None and len(other_sheets) < MAX_SHEET_NAMES:
                other_sheets.append(name)
    if chosen is None or scan is None:
        raise InputError("empty_document")
    if scan.last_row > MAX_ROWS_READ:
        raise InputError("too_many_rows")

    chosen.reset_dimensions()  # never trust the <dimension> the file declares
    builder = TableBuilder()
    rows = chosen.iter_rows(min_row=1, max_row=scan.last_row, min_col=1, max_col=scan.last_column)
    for number, cells in enumerate(rows, start=1):
        if number in scan.hidden_rows:
            continue
        builder.add_row(
            number,
            (
                (column, render_cell(cell.value, getattr(cell, "number_format", None)))
                for column, cell in enumerate(cells, start=1)
                if column not in scan.hidden_columns
            ),
        )
    warning_codes: list[InputWarningCode] = []
    if others:
        warning_codes.append("other_sheets_ignored")
    if hidden_with_data:
        warning_codes.append("hidden_sheets_ignored")
    if scan.hidden_row_count or scan.hidden_column_count:
        warning_codes.append("hidden_cells_ignored")
    if scan.formulas_without_value:
        warning_codes.append("formula_without_value")
    if macros:
        warning_codes.append("macros_ignored")
    table = builder.build(
        other_sheets=tuple(other_sheets),
        hidden_rows=scan.hidden_row_count,
        hidden_columns=scan.hidden_column_count,
        formulas_without_value=scan.formulas_without_value,
    )
    return SpreadsheetParse(table=table, warnings=tuple(warning_codes))

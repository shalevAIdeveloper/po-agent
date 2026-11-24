"""XLSX with openpyxl (then repacked deterministically) and CSV as explicit bytes.

- XLSX: openpyxl writes the workbook; the package is then rewritten with entries sorted
  by name, a fixed ZipInfo (date, system, permissions) and the core properties' created
  and modified times fixed (openpyxl stamps them with the clock). Committed files are
  compared by ZIP entries (owner decision Q2), so the platform's zlib doesn't matter.
- CSV: Python's csv writer with the case's delimiter and line ending, encoded with the
  case's encoding; the bytes are exactly what the case asks for.

`shown(value)` is the text the PR 5 parser reads for a cell, used for `raw_text` and
the oracle's header values; the fixture tests check it against the parsed table.
"""

import csv
import io
import re
import zipfile
from datetime import date
from decimal import Decimal
from typing import Final

import openpyxl
from openpyxl.utils import get_column_letter

from evals.generate.spec import CellValue, SheetDoc

FIXED_TIME: Final = "2026-11-01T00:00:00Z"
_ZIP_TIME: Final = (2026, 11, 1, 0, 0, 0)
_CORE_TIME_RE: Final = re.compile(
    r"(<dcterms:(?:created|modified)[^>]*>)[^<]*(</dcterms:(?:created|modified)>)"
)


def shown(value: CellValue, kind: str) -> str:
    """The cell's text as the parser renders it (XLSX numbers lose trailing zeros)."""
    if isinstance(value, str):
        return value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, int) or kind == "csv":
        return str(value)
    number = float(value)
    if number == 0:
        return "0"
    return format(Decimal(f"{number:.15g}"), "f")


def _xlsx_value(value: CellValue) -> object:
    return float(value) if isinstance(value, Decimal) else value


def _repack(data: bytes) -> bytes:
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(data)) as source,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for name in sorted(source.namelist()):
            payload = source.read(name)
            if name == "docProps/core.xml":
                payload = _CORE_TIME_RE.sub(rf"\g<1>{FIXED_TIME}\g<2>", payload.decode()).encode()
            info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            target.writestr(info, payload)
    return out.getvalue()


def render_xlsx(doc: SheetDoc) -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    if sheet is None:
        raise RuntimeError("openpyxl gave no active sheet")
    sheet.title = doc.sheet_title
    for row_number, row in enumerate(doc.rows, start=1):
        for column, value in enumerate(row, start=1):
            if value is not None:
                sheet.cell(row=row_number, column=column, value=_xlsx_value(value))
    for ref, number_format in doc.formats:
        sheet[ref].number_format = number_format
    for letter, width in doc.widths:
        sheet.column_dimensions[letter].width = width
    for cells in doc.merges:
        sheet.merge_cells(cells)
    workbook.properties.creator = "evals.generate"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return _repack(buffer.getvalue())


def render_csv(doc: SheetDoc) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=doc.delimiter, lineterminator=doc.newline)
    for row in doc.rows:
        cells = ["" if value is None else shown(value, "csv") for value in row]
        while cells and cells[-1] == "":
            cells.pop()
        writer.writerow(cells)
    return buffer.getvalue().encode(doc.encoding)


def render(doc: SheetDoc) -> bytes:
    return render_xlsx(doc) if doc.kind == "xlsx" else render_csv(doc)


def letter(column: int) -> str:
    return str(get_column_letter(column))

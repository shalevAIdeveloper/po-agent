"""Spreadsheet cases (M1 PR 6 T7): the parser reads exactly the grid the spec holds."""

import io
import zipfile

import pytest

from app.extraction.inputs import ParsedDocument, run_parsers
from evals.generate import render_sheet
from evals.generate.cases import CASES
from evals.generate.spec import Case, SheetDoc
from evals.run import FIXTURES_DIR

SHEETS = [pytest.param(c, id=c.name) for c in CASES if isinstance(c.document, SheetDoc)]
XLSX = [p for p in SHEETS if p.values[0].input_type == "xlsx"]  # type: ignore[union-attr]
CSV = [p for p in SHEETS if p.values[0].input_type == "csv"]  # type: ignore[union-attr]


def _doc(case: Case) -> SheetDoc:
    assert isinstance(case.document, SheetDoc)
    return case.document


def _source(case: Case) -> bytes:
    return (FIXTURES_DIR / case.name / case.source_name).read_bytes()


def _grid(case: Case) -> dict[int, dict[int, str]]:
    """Row number -> column number -> the text the parser should read."""
    doc = _doc(case)
    grid: dict[int, dict[int, str]] = {}
    for number, row in enumerate(doc.rows, start=1):
        cells = {
            column: render_sheet.shown(value, doc.kind)
            for column, value in enumerate(row, start=1)
            if value is not None
        }
        if cells:
            grid[number] = cells
    return grid


@pytest.mark.parametrize("case", SHEETS)
def test_the_parser_reads_exactly_the_spec_grid(case: Case) -> None:
    parsed = run_parsers(_source(case))
    assert isinstance(parsed, ParsedDocument)
    assert parsed.table is not None
    read = {
        row.number: {
            column: cell
            for column, cell in zip(parsed.table.columns, row.cells, strict=True)
            if cell is not None
        }
        for row in parsed.table.rows
    }
    assert read == _grid(case)


@pytest.mark.parametrize("case", SHEETS)
def test_a_sheet_renders_the_same_bytes_twice(case: Case) -> None:
    assert render_sheet.render(_doc(case)) == render_sheet.render(_doc(case))


@pytest.mark.parametrize("case", XLSX)
def test_the_xlsx_package_is_normalized(case: Case) -> None:
    with zipfile.ZipFile(io.BytesIO(_source(case))) as archive:
        infos = archive.infolist()
        core = archive.read("docProps/core.xml").decode()
    names = [i.filename for i in infos]
    assert names == sorted(names)
    assert {i.date_time for i in infos} == {(2026, 11, 1, 0, 0, 0)}
    assert {i.create_system for i in infos} == {3}
    assert core.count(render_sheet.FIXED_TIME) == 2


def test_merged_cells_and_a_date_cell_are_really_in_the_xlsx() -> None:
    by_name = {c.name: c for c in CASES}
    with zipfile.ZipFile(io.BytesIO(_source(by_name["xlsx_merged_cells"]))) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode()
    assert '<mergeCell ref="A1:H1"' in sheet
    assert '<mergeCell ref="C5:D5"' in sheet
    with zipfile.ZipFile(io.BytesIO(_source(by_name["xlsx_header_block_row9"]))) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode()
    assert '<c r="B4" s="1" t="n"><v>46338</v></c>' in sheet  # a date serial, not text


@pytest.mark.parametrize("case", CSV)
def test_csv_bytes_use_the_case_delimiter_and_crlf(case: Case) -> None:
    data = _source(case)
    doc = _doc(case)
    text = data.decode(doc.encoding)
    assert not data.startswith(b"\xef\xbb\xbf")
    records = text.split("\r\n")
    assert records[-1] == ""
    assert doc.delimiter in records[0]


def test_the_quoted_csv_keeps_commas_quotes_and_a_line_break_inside_fields() -> None:
    case = next(c for c in CASES if c.name == "csv_comma_quoted_fields")
    text = _source(case).decode()
    assert '"Rope, 1/2"" nylon, ""marine grade"", 600 ft"' in text
    assert '"Battery charger, 12V\nwith clamps and cables"' in text

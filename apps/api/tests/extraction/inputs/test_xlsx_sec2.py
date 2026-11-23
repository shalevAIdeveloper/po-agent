"""`sec` re-check findings N1-N3 on the XLSX parser (M1 PR 5)."""

import io
import time
import tracemalloc

import pytest

from app.extraction.inputs import xlsx
from app.extraction.inputs.detect import detect_kind
from app.extraction.inputs.errors import InputError
from app.extraction.inputs.limits import MAX_SHEETS
from app.extraction.inputs.xlsx import parse_xlsx
from app.extraction.inputs.xlsx_scan import scan_sheet
from tests.extraction.inputs.sheets import (
    CT_NS,
    NS,
    PKG_REL_NS,
    REL_NS,
    SHEET_CT,
    XLSM_MAIN,
    XLSX_MAIN,
    XML_DECL,
    Sheet,
    inline,
    make_xlsx,
    row,
    sheet_xml,
    text_rows,
)


def code_of(data: bytes) -> str:
    """Detection, then the parser, as `run_parsers` does."""
    try:
        detect_kind(data)
        parse_xlsx(data)
    except InputError as error:
        return error.code
    return "parsed"


def parser_code(data: bytes) -> str:
    try:
        parse_xlsx(data)
    except InputError as error:
        return error.code
    return "parsed"


def _types(*overrides: tuple[str, str], default_xml: str = "application/xml") -> str:
    parts = "".join(f'<Override PartName="{n}" ContentType="{t}"/>' for n, t in overrides)
    return (
        f'{XML_DECL}<Types xmlns="{CT_NS}">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        f'<Default Extension="xml" ContentType="{default_xml}"/>'
        f'{parts}<Override PartName="/xl/worksheets/sheet1.xml" ContentType="{SHEET_CT}"/>'
        "</Types>"
    )


# --- N1: only /xl/workbook.xml may be the workbook part ---------------------------------


def _decoy(sheets: int) -> bytes:
    """`/xl/evil.xml` listed first (openpyxl reads it); `/xl/workbook.xml` a decoy."""
    entries = "".join(
        f'<sheet name="S{i}" sheetId="{i}" r:id="rId1"/>' for i in range(1, sheets + 1)
    )
    evil = (
        f'{XML_DECL}<workbook xmlns="{NS}" xmlns:r="{REL_NS}"><sheets>{entries}</sheets></workbook>'
    )
    rels = (
        f'{XML_DECL}<Relationships xmlns="{PKG_REL_NS}"><Relationship Id="rId1" '
        f'Type="{REL_NS}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
    )
    return make_xlsx(
        content_types=_types(("/xl/evil.xml", XLSX_MAIN), ("/xl/workbook.xml", XLSX_MAIN)),
        extra=[("xl/evil.xml", evil), ("xl/_rels/evil.xml.rels", rels)],
    )


def test_a_decoy_workbook_part_is_refused_fast() -> None:
    data = _decoy(50_000)
    start = time.perf_counter()
    assert code_of(data) == "invalid_file"
    assert parser_code(data) == "invalid_file"  # the parser checks on its own too
    assert time.perf_counter() - start < 2


@pytest.mark.parametrize(
    "types",
    [
        _types(("/xl/other.xml", XLSX_MAIN)),  # the workbook elsewhere
        _types(("/xl/workbook.xml", XLSX_MAIN), ("/xl/b.xml", XLSM_MAIN)),  # two workbooks
        _types(default_xml=XLSX_MAIN),  # typed by a Default entry
        _types(("/xl/workbook.xml", XLSX_MAIN), default_xml=XLSX_MAIN),
    ],
    ids=["elsewhere", "two", "default-only", "default-and-override"],
)
def test_the_workbook_part_must_be_the_one_override(types: str) -> None:
    data = make_xlsx(content_types=types)
    assert code_of(data) == "invalid_file"
    assert parser_code(data) == "invalid_file"


def test_the_normal_package_still_parses() -> None:
    assert code_of(make_xlsx(content_types=_types(("/xl/workbook.xml", XLSX_MAIN)))) == "parsed"


# --- N2: the sheet count streams and stops early ----------------------------------------


def test_a_huge_sheet_list_is_counted_in_bounded_time_and_memory() -> None:
    entries = "".join(f'<sheet name="S{i}" sheetId="{i}" r:id="rId1"/>' for i in range(200_000))
    workbook = (
        f'{XML_DECL}<workbook xmlns="{NS}" xmlns:r="{REL_NS}"><sheets>{entries}</sheets></workbook>'
    )
    data = make_xlsx(workbook=workbook)
    tracemalloc.start()
    start = time.perf_counter()
    try:
        assert parser_code(data) == "document_too_large"
        elapsed = time.perf_counter() - start
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert elapsed < 2
    assert peak < 20 * 1024 * 1024  # a full parse of 200k elements is ~100 MB


def test_the_count_stops_at_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    sheets = [Sheet(f"S{i}", sheet_xml(text_rows([["x"]]))) for i in range(3)]
    monkeypatch.setattr(xlsx, "MAX_SHEETS", 3)
    assert parser_code(make_xlsx(sheets)) == "parsed"
    monkeypatch.setattr(xlsx, "MAX_SHEETS", 2)
    assert parser_code(make_xlsx(sheets)) == "document_too_large"
    assert MAX_SHEETS == 64


# --- N3: memory stays bounded inside a cell ------------------------------------------------


def test_junk_inside_a_cell_keeps_the_scan_memory_flat() -> None:
    junk = "<x/>" * 300_000
    xml = (
        f'{XML_DECL}<worksheet xmlns="{NS}"><sheetData><row r="1">'
        f'<c r="A1" t="inlineStr"><is><t>a</t></is>{junk}</c>'
        f'<c r="B1"><f>1+1</f>{junk}</c></row></sheetData></worksheet>'
    ).encode()
    tracemalloc.start()
    try:
        scan = scan_sheet(io.BytesIO(xml))
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    # The cell's own value and formula are still seen.
    assert scan.has_content and scan.formulas_without_value == 1
    assert peak < 8 * 1024 * 1024  # kept, 600k elements are ~200 MB


def test_a_cell_value_after_junk_is_still_read() -> None:
    xml = sheet_xml(row(1, '<c r="A1"><x><y/></x><v>7</v></c>' + inline("B1", "b")))
    assert parse_xlsx(make_xlsx([Sheet("PO", xml)])).table.rows[0].cells == ("7", "b")


def test_too_many_text_runs_in_one_cell_are_invalid() -> None:
    runs = "<t>a</t>" * 10_001
    xml = sheet_xml(row(1, f'<c r="A1" t="inlineStr"><is>{runs}</is></c>'))
    with pytest.raises(InputError, match="invalid_file"):
        scan_sheet(io.BytesIO(xml.encode()))


def test_inline_rich_text_runs_are_kept() -> None:
    cell = '<c r="A1" t="inlineStr"><is><r><rPr><b/></rPr><t>PO </t></r><r><t>45</t></r></is></c>'
    assert parse_xlsx(make_xlsx([Sheet("PO", sheet_xml(row(1, cell)))])).table.rows[0].cells == (
        "PO 45",
    )

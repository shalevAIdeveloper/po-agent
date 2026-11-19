"""`sec` review findings on the XLSX parser (M1 PR 5), one regression test each."""

import time
from typing import Any

import openpyxl.reader.excel as excel
import pytest

from app.extraction.inputs import xlsx
from app.extraction.inputs.errors import InputError
from app.extraction.inputs.limits import MAX_CELLS_READ, MAX_SHEETS
from app.extraction.inputs.xlsx import parse_xlsx
from tests.extraction.inputs.sheets import (
    NS,
    REL_NS,
    XML_DECL,
    Sheet,
    inline,
    make_xlsx,
    row,
    sheet_xml,
    text_rows,
)


def code_of(data: bytes) -> str:
    try:
        parse_xlsx(data)
    except InputError as error:
        return error.code
    return "parsed"


# --- M1: chart sheets are never read, wherever their part lives ---------------------------

_CHARTSHEET = (
    f'{XML_DECL}<chartsheet xmlns="{NS}" xmlns:r="{REL_NS}"><sheetViews>'
    '<sheetView workbookViewId="0"/></sheetViews></chartsheet>'
)


def _workbook_with_chartsheet(target: str) -> bytes:
    return make_xlsx(
        extra=[(f"xl/{target}", _CHARTSHEET)],
        workbook_rels_extra=(
            f'<Relationship Id="rIdC" Type="{REL_NS}/chartsheet" Target="{target}"/>'
        ),
        workbook=(
            f'{XML_DECL}<workbook xmlns="{NS}" xmlns:r="{REL_NS}"><sheets>'
            '<sheet name="PO" sheetId="1" r:id="rId1"/>'
            '<sheet name="Chart" sheetId="2" r:id="rIdC"/></sheets></workbook>'
        ),
    )


@pytest.mark.parametrize("target", ["worksheets/chart.xml", "chartsheets/sheet1.xml", "c.xml"])
def test_a_chartsheet_is_never_read_whatever_its_path(
    monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    # openpyxl picks chart sheets by relationship type; their drawings reach Pillow.
    called: list[str] = []

    def spy(_self: Any, _sheet: Any, rel: Any) -> None:
        called.append(rel.target)

    def no_images(*_args: object) -> Any:
        raise AssertionError("a drawing was read")

    monkeypatch.setattr(excel.ExcelReader, "read_chartsheet", spy)
    monkeypatch.setattr(excel, "find_images", no_images)
    parsed = parse_xlsx(_workbook_with_chartsheet(target))
    assert called == []
    assert parsed.table.rows[0].cells == ("x",)
    assert parsed.warnings == ()


# --- M2: a tiny file can't hold the child for the whole timeout ----------------------------


def test_a_huge_sparse_area_is_refused_before_reading() -> None:
    # A1 and XFD20000: two cells, a 327M-cell read; took about 90 s.
    data = make_xlsx(
        [Sheet("PO", sheet_xml(row(1, inline("A1", "a")) + row(20_000, inline("XFD20000", "b"))))]
    )
    start = time.perf_counter()
    assert code_of(data) == "document_too_large"
    assert time.perf_counter() - start < 2


def test_the_area_cap_is_rows_times_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    assert MAX_CELLS_READ == 2_000_000
    data = make_xlsx([Sheet("PO", sheet_xml(text_rows([["a", "b"], ["c", "d"]])))])
    monkeypatch.setattr(xlsx, "MAX_CELLS_READ", 4)
    assert code_of(data) == "parsed"
    monkeypatch.setattr(xlsx, "MAX_CELLS_READ", 3)
    assert code_of(data) == "document_too_large"


def test_too_many_sheets_are_refused() -> None:
    sheets = [Sheet(f"S{i}", sheet_xml("")) for i in range(MAX_SHEETS)]
    assert (
        code_of(make_xlsx([*sheets[:-1], Sheet("PO", sheet_xml(text_rows([["x"]])))])) == "parsed"
    )
    assert (
        code_of(make_xlsx([*sheets, Sheet("PO", sheet_xml(text_rows([["x"]])))]))
        == "document_too_large"
    )


def test_each_sheet_part_is_scanned_once(monkeypatch: pytest.MonkeyPatch) -> None:
    # Many sheets naming the same part must not multiply the scan work.
    rels = "".join(
        f'<Relationship Id="rIdD{i}" Type="{REL_NS}/worksheet" Target="worksheets/sheet1.xml"/>'
        for i in range(10)
    )
    sheets = "".join(f'<sheet name="D{i}" sheetId="{i + 2}" r:id="rIdD{i}"/>' for i in range(10))
    data = make_xlsx(
        workbook_rels_extra=rels,
        workbook=(
            f'{XML_DECL}<workbook xmlns="{NS}" xmlns:r="{REL_NS}"><sheets>'
            f'<sheet name="PO" sheetId="1" r:id="rId1"/>{sheets}</sheets></workbook>'
        ),
    )
    scans: list[str] = []
    real = xlsx.scan_sheet

    def counting(source: Any, **kwargs: Any) -> Any:
        scans.append("x")
        return real(source, **kwargs)

    monkeypatch.setattr(xlsx, "scan_sheet", counting)
    parsed = parse_xlsx(data)
    assert len(scans) == 1
    assert parsed.warnings == ("other_sheets_ignored",)
    assert len(parsed.table.other_sheets) == 10

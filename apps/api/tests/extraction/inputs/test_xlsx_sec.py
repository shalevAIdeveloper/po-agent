"""`sec` review findings on the XLSX parser (M1 PR 5), one regression test each."""

from typing import Any

import openpyxl.reader.excel as excel
import pytest

from app.extraction.inputs.xlsx import parse_xlsx
from tests.extraction.inputs.sheets import (
    NS,
    REL_NS,
    XML_DECL,
    make_xlsx,
)

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

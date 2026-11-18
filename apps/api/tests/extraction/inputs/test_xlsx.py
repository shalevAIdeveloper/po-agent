import io
from datetime import date, datetime, time, timedelta

import pytest

from app.extraction.inputs import xlsx
from app.extraction.inputs.errors import InputError
from app.extraction.inputs.limits import MAX_ROWS_READ, MAX_SHEET_NAME_CHARS
from app.extraction.inputs.table import SheetTable, SpreadsheetParse
from app.extraction.inputs.xlsx import parse_xlsx, render_cell
from app.extraction.inputs.xlsx_scan import scan_sheet
from tests.extraction.inputs.sheets import (
    XLSM_MAIN,
    Sheet,
    formula,
    inline,
    make_openpyxl_xlsx,
    make_xlsx,
    number,
    row,
    shared,
    sheet_xml,
    text_rows,
)

PO_ROWS = [
    ["PO Number", "4500012345"],
    [],
    ["Line", "Item", "Description", "Qty", "Price"],
    [1, "00123-A", "Widget, blue", 12, 1234.5],
    [2, "00124", "Bolt", 3, 0.25],
]


def parse(data: bytes) -> SpreadsheetParse:
    return parse_xlsx(data)


def table_of(data: bytes) -> SheetTable:
    return parse_xlsx(data).table


def code_of(data: bytes) -> str:
    """As `run_parsers` reports it: any other exception is `invalid_file`."""
    try:
        parse_xlsx(data)
    except InputError as error:
        return error.code
    except Exception:
        return "invalid_file"
    raise AssertionError("parsed")


def one_sheet(rows_xml: str, *, cols: str = "", **kwargs: object) -> bytes:
    return make_xlsx([Sheet("PO", sheet_xml(rows_xml, cols=cols))], **kwargs)  # type: ignore[arg-type]


# --- happy path -----------------------------------------------------------------------


def test_an_openpyxl_workbook_becomes_a_table() -> None:
    parsed = parse(make_openpyxl_xlsx(PO_ROWS))
    assert parsed.warnings == ()
    table = parsed.table
    assert table.columns == (1, 2, 3, 4, 5)
    assert [r.number for r in table.rows] == [1, 3, 4, 5]
    assert table.rows[0].cells == ("PO Number", "4500012345", None, None, None)
    assert table.rows[2].cells == ("1", "00123-A", "Widget, blue", "12", "1234.5")
    assert table.rows[3].cells == ("2", "00124", "Bolt", "3", "0.25")


def test_shared_strings_and_inline_strings() -> None:
    data = one_sheet(row(1, shared("A1", 1), inline("B1", "inline")), shared_strings=["x", "PO"])
    assert table_of(data).rows[0].cells == ("PO", "inline")


def test_number_formats_dates_and_booleans() -> None:
    data = make_openpyxl_xlsx(
        [[123, datetime(2026, 11, 16), datetime(2026, 11, 16, 9, 30), True, 0.1 + 0.2, 1e20]],
        formats={"A1": "00000"},
    )
    assert table_of(data).rows[0].cells == (
        "00123",
        "2026-11-16",
        "2026-11-16T09:30:00",
        "TRUE",
        "0.3",
        "100000000000000000000",
    )


@pytest.mark.parametrize(
    ("value", "number_format", "text"),
    [
        (None, None, None),
        (12, "General", "12"),
        (123, "000000", "000123"),
        (-5, "000", "-5"),
        (12.0, "General", "12"),
        (123.0, "00000", "00123"),
        (1234.5, None, "1234.5"),
        (-0.0, None, "0"),
        (1 / 3, None, "0.333333333333333"),
        (1.5e-7, None, "0.00000015"),
        (float("inf"), None, None),
        (False, None, "FALSE"),
        (date(2026, 3, 4), None, "2026-03-04"),
        (time(9, 30), None, "09:30:00"),
        (timedelta(hours=26), None, "1 day, 2:00:00"),
        ("#N/A", None, "#N/A"),
        (object(), None, None),
    ],
)
def test_render_cell(value: object, number_format: str | None, text: str | None) -> None:
    assert render_cell(value, number_format) == text


def test_the_first_visible_sheet_with_data_is_read_and_others_are_named() -> None:
    data = make_xlsx(
        [
            Sheet("Empty", sheet_xml("")),
            Sheet("Secret", sheet_xml(text_rows([["HIDDEN-SHEET-DATA"]])), state="hidden"),
            Sheet("Order", sheet_xml(text_rows([["Item", "Qty"], ["00123-A", "12"]]))),
            Sheet("Notes\nfor <b>you</b>", sheet_xml(text_rows([["note"]]))),
            Sheet("Blank", sheet_xml("")),
            Sheet("x" * 31, sheet_xml(text_rows([["more"]])), state="veryHidden"),
        ]
    )
    parsed = parse(data)
    assert parsed.table.rows[0].cells == ("Item", "Qty")
    assert parsed.table.other_sheets == ("Notes for <b>you</b>",)
    assert parsed.warnings == ("other_sheets_ignored", "hidden_sheets_ignored")
    assert "HIDDEN-SHEET-DATA" not in repr(parsed.table.rows)


def test_other_sheet_names_are_capped() -> None:
    data = make_xlsx(
        [Sheet("A", sheet_xml(text_rows([["a"]]))), Sheet("B" * 31, sheet_xml(text_rows([["b"]])))]
    )
    assert parse(data).table.other_sheets == ("B" * MAX_SHEET_NAME_CHARS,)


def test_an_empty_hidden_sheet_gives_no_warning() -> None:
    data = make_xlsx(
        [Sheet("PO", sheet_xml(text_rows([["a"]]))), Sheet("H", sheet_xml(""), state="hidden")]
    )
    assert parse(data).warnings == ()


def test_a_workbook_with_no_visible_data_is_empty() -> None:
    hidden_only = make_xlsx([Sheet("H", sheet_xml(text_rows([["a"]])), state="hidden")])
    assert code_of(hidden_only) == "empty_document"
    assert code_of(make_xlsx([Sheet("E", sheet_xml(""))])) == "empty_document"


# --- hidden rows and columns ----------------------------------------------------------


def test_a_hidden_row_is_never_read() -> None:
    rows = (
        row(1, inline("A1", "Item"), inline("B1", "Qty"))
        + row(2, inline("A2", "00123-A"), inline("B2", "12"))
        + row(3, inline("A3", "HIDDEN-ROW"), inline("B3", "999"), attrs=' hidden="1"')
        + row(4, inline("A4", "00124"), inline("B4", "3"))
    )
    parsed = parse(one_sheet(rows))
    assert [r.number for r in parsed.table.rows] == [1, 2, 4]
    assert "HIDDEN-ROW" not in str([r.cells for r in parsed.table.rows])
    assert parsed.warnings == ("hidden_cells_ignored",)
    assert (parsed.table.hidden_rows, parsed.table.hidden_columns) == (1, 0)


def test_an_autofilter_hidden_row_and_a_zero_height_row_are_dropped() -> None:
    rows = (
        row(1, inline("A1", "Item"))
        + row(2, inline("A2", "FILTERED"), attrs=' hidden="true" customHeight="1"')
        + row(3, inline("A3", "ZERO-HEIGHT"), attrs=' ht="0" customHeight="1"')
        + row(4, inline("A4", "00124"))
    )
    extra = '<autoFilter ref="A1:A4"/>'
    data = make_xlsx([Sheet("PO", sheet_xml(rows, extra=extra))])
    parsed = parse(data)
    assert [r.cells for r in parsed.table.rows] == [("Item",), ("00124",)]
    assert parsed.table.hidden_rows == 2


def test_hidden_and_zero_width_columns_are_never_read() -> None:
    rows = row(
        1,
        inline("A1", "Item"),
        inline("B1", "HIDDEN-COL"),
        inline("C1", "ZERO-WIDTH"),
        inline("D1", "Qty"),
    ) + row(
        2, inline("A2", "00123-A"), inline("B2", "secret"), inline("C2", "x"), inline("D2", "12")
    )
    cols = '<col min="2" max="2" hidden="1" width="9"/><col min="3" max="3" width="0"/>'
    parsed = parse(one_sheet(rows, cols=cols))
    assert parsed.table.columns == (1, 4)
    assert [r.cells for r in parsed.table.rows] == [("Item", "Qty"), ("00123-A", "12")]
    assert parsed.warnings == ("hidden_cells_ignored",)
    assert parsed.table.hidden_columns == 2


def test_an_empty_hidden_row_or_column_gives_no_warning() -> None:
    rows = row(1, inline("A1", "a")) + row(2, attrs=' hidden="1"') + row(3, inline("A3", "b"))
    parsed = parse(one_sheet(rows, cols='<col min="5" max="16384" hidden="1"/>'))
    assert parsed.warnings == ()


# --- formulas ---------------------------------------------------------------------------


def test_a_formula_gives_its_cached_value_never_its_text() -> None:
    rows = row(1, number("A1", "2"), formula("B1", 'HYPERLINK("http://x.example","CLICK")', "42"))
    assert table_of(one_sheet(rows)).rows[0].cells == ("2", "42")


def test_an_uncached_formula_is_empty_with_a_warning() -> None:
    rows = row(
        1, inline("A1", "Total"), formula("B1", "SUM(B2:B9)", None), formula("C1", "1+1", "")
    ) + row(
        2,
        inline("A2", "x"),
        formula(
            "B2",
            "NOW()",
            None,
        ),
    )
    parsed = parse(one_sheet(rows))
    assert [r.cells for r in parsed.table.rows] == [("Total",), ("x",)]
    assert parsed.warnings == ("formula_without_value",)
    assert parsed.table.formulas_without_value == 3


def test_an_uncached_formula_in_a_hidden_row_is_not_counted() -> None:
    rows = row(1, inline("A1", "a")) + row(2, formula("A2", "1+1", None), attrs=' hidden="1"')
    assert parse(one_sheet(rows)).warnings == ()


# --- macros, chart sheets ----------------------------------------------------------------


def test_macros_are_never_read() -> None:
    data = make_xlsx(main_type=XLSM_MAIN, extra=[("xl/vbaProject.bin", b"\xd0\xcf\x11\xe0VBA")])
    parsed = parse(data)
    assert parsed.warnings == ("macros_ignored",)
    assert parsed.table.rows[0].cells == ("x",)


def test_a_vba_project_in_an_xlsx_is_flagged_too() -> None:
    assert parse(make_xlsx(extra=[("xl/vbaProject.bin", b"x")])).warnings == ("macros_ignored",)


# --- order and bounds ----------------------------------------------------------------------


def test_rows_without_numbers_follow_openpyxl() -> None:
    rows = '<row><c t="inlineStr"><is><t>a</t></is></c><c t="inlineStr"><is><t>b</t></is></c></row>'
    rows += "<row><c><v>1</v></c></row>"
    table = table_of(one_sheet(rows))
    assert [r.number for r in table.rows] == [1, 2]
    assert [r.cells for r in table.rows] == [("a", "b"), ("1", None)]


@pytest.mark.parametrize(
    "rows",
    [
        row(2, inline("A2", "a")) + row(1, inline("A1", "b")),  # rows out of order
        row(1, inline("B1", "a"), inline("A1", "b")),  # cells out of order
        row(1, inline("A1", "a"), inline("A1", "b")),  # the same cell twice
        row(1, inline("1A", "a")),  # a bad reference
        '<row r="x"/>',
        row(1, inline("XFE1", "a")),  # past Excel's last column
        row(1_048_577, inline("A1048577", "a")),
    ],
)
def test_ambiguous_or_out_of_grid_sheets_are_invalid(rows: str) -> None:
    assert code_of(one_sheet(rows)) == "invalid_file"


def test_data_below_the_rows_read_cap_is_too_many_rows() -> None:
    far = MAX_ROWS_READ + 1
    data = one_sheet(row(1, inline("A1", "a")) + row(far, inline(f"A{far}", "b")))
    assert code_of(data) == "too_many_rows"


def test_a_lying_dimension_is_ignored() -> None:
    xml = sheet_xml(text_rows([["a", "b"], ["c", "d"]])).replace(
        "<sheetData>", '<dimension ref="A1:A1"/><sheetData>'
    )
    assert table_of(make_xlsx([Sheet("PO", xml)])).columns == (1, 2)


def test_the_scan_bounds_the_read() -> None:
    xml = sheet_xml(
        row(1, inline("A1", "a")) + row(5, inline("C5", "b"), attrs=' hidden="1"')
    ).encode()
    scan = scan_sheet(io.BytesIO(xml))
    assert (scan.last_row, scan.last_column, scan.hidden_rows) == (1, 1, frozenset({5}))


def test_openpyxl_parses_with_defusedxml() -> None:
    assert xlsx.xml_is_hardened()

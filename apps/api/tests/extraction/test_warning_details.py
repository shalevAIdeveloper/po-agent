"""Warnings with details (owner, approved plan item (a)) and skipped rows (owner, M4)."""

import json

import pytest
import structlog

from app.extraction.column_mapping import mapping_settings
from app.extraction.extract import DocumentInput, extract_po
from app.extraction.grounding import ExtractionWarning, WarningDetails
from app.extraction.inputs import parse_document
from app.extraction.inputs.limits import MAX_SHEET_NAME_CHARS, MAX_SHEET_NAMES, MAX_TABLE_ROWS
from app.extraction.inputs.table import SheetTable, TableRow
from app.extraction.spreadsheet import extract_spreadsheet
from app.llm.fake import FakeLLMClient, FakeResponse
from tests.extraction.inputs.sheets import Sheet, inline, make_xlsx, row, sheet_xml, text_rows
from tests.extraction.test_column_mapping import mapping
from tests.extraction.test_extract import output
from tests.extraction.test_spreadsheet import STANDARD, mapped

SETTINGS = mapping_settings("claude-haiku-5-5")
SECRET_SHEET = "SECRET-SHEET-9f"


def with_details(table: SheetTable, **changes: object) -> SheetTable:
    from dataclasses import replace

    return replace(table, **changes)  # type: ignore[arg-type]


def detailed(result: object) -> dict[str, WarningDetails | None]:
    return {w.code: w.details for w in result.warnings}  # type: ignore[attr-defined]


# --- B: details on the extraction's warnings ---------------------------------------------


async def test_other_sheets_hidden_cells_and_formulas_carry_details() -> None:
    table = with_details(
        STANDARD,
        other_sheets=(SECRET_SHEET, "Notes"),
        hidden_rows=2,
        hidden_columns=1,
        formulas_without_value=3,
    )
    client = FakeLLMClient([FakeResponse(mapped())])
    result = await extract_spreadsheet(client, table, SETTINGS)
    details = detailed(result)
    assert details["other_sheets_ignored"] == WarningDetails(names=(SECRET_SHEET, "Notes"))
    assert details["hidden_cells_ignored"] == WarningDetails(hidden_rows=2, hidden_columns=1)
    assert details["formula_without_value"] == WarningDetails(count=3)
    # Never to the model.
    assert SECRET_SHEET not in json.dumps(
        [getattr(part, "text", "") for request in client.requests for part in request.content]
    )


async def test_the_other_sheets_warning_comes_without_names_too() -> None:
    client = FakeLLMClient([FakeResponse(mapped())])
    result = await extract_spreadsheet(
        client, STANDARD, SETTINGS, input_warnings=("other_sheets_ignored",)
    )
    assert detailed(result)["other_sheets_ignored"] == WarningDetails()


async def test_from_a_real_workbook_through_parse_document() -> None:
    rows = text_rows([["Item", "Qty"], ["00123-A", "12"]]) + row(
        3, inline("A3", "HIDDEN"), attrs=' hidden="1"'
    )
    data = make_xlsx(
        [Sheet("PO", sheet_xml(rows)), Sheet(SECRET_SHEET, sheet_xml(text_rows([["x"]])))]
    )
    document = parse_document(data)
    assert document.table is not None
    response = json.dumps(
        {
            "schema_version": "column_mapping.v1",
            "document_type": "purchase_order",
            "first_data_row": 2,
            "columns": [{"column": "A", "field": "customer_part_number"}],
            "header_values": [],
            "addresses": [],
            "footer_rows": [],
            "confidence": 0.8,
        }
    )
    with structlog.testing.capture_logs() as logs:
        result = await extract_spreadsheet(
            FakeLLMClient([FakeResponse(response)]),
            document.table,
            SETTINGS,
            input_warnings=document.warnings,
        )
    details = detailed(result)
    assert details["other_sheets_ignored"] == WarningDetails(names=(SECRET_SHEET,))
    assert details["hidden_cells_ignored"] == WarningDetails(hidden_rows=1, hidden_columns=0)
    assert SECRET_SHEET not in repr(logs) and "HIDDEN" not in repr(logs)


async def test_pdf_warnings_have_no_details() -> None:
    result = await extract_po(
        FakeLLMClient([FakeResponse(output())]),
        DocumentInput(text="PO 4500012345"),
        mapping_settings("m"),
    )
    assert result.warnings
    assert all(w.details is None for w in result.warnings)
    assert ExtractionWarning(code="ungrounded", path="x") == ExtractionWarning(
        code="ungrounded", path="x", details=None
    )


def test_the_repr_hides_names_and_texts() -> None:
    warning = ExtractionWarning(
        code="rows_skipped",
        path="lines",
        details=WarningDetails(names=(SECRET_SHEET,), rows=(4,), texts=("SECRET-ROW",)),
    )
    assert SECRET_SHEET not in repr(warning) and "SECRET-ROW" not in repr(warning)


@pytest.mark.parametrize(
    "bad",
    [
        {"names": ("x" * (MAX_SHEET_NAME_CHARS + 1),)},
        {"names": ("a",) * (MAX_SHEET_NAMES + 1)},
        {"names": ("a\nb",)},
        {"names": ("",)},
        {"rows": (0,), "texts": ("a",)},
        {"rows": (2, 1), "texts": ("a", "b")},  # increasing
        {"rows": (1,), "texts": ()},  # one text per row
        {"rows": tuple(range(1, MAX_TABLE_ROWS + 2)), "texts": ("a",) * (MAX_TABLE_ROWS + 1)},
        {"rows": (1,), "texts": ("a\x00b",)},
        {"rows": (1,), "texts": ("‮a",)},
        {"rows": (1,), "texts": ("  ",)},
        {"count": -1},
        {"hidden_rows": -1},
    ],
)
def test_details_are_typed_and_bounded(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        WarningDetails(**bad)  # type: ignore[arg-type]


# --- A (M4): skipped rows with data never disappear silently --------------------------


def _po(lines: int) -> SheetTable:
    rows = [
        TableRow(1, ("PO#: 4500012345", None, None, None)),
        TableRow(2, ("Ship to", "Acme Corp", None, None)),
        TableRow(3, (None, "1 Main St", None, None)),
        TableRow(5, ("Item", "Description", "Qty", "Price")),
    ]
    rows += [TableRow(6 + n, (f"P{n}", f"Widget {n}", str(n + 1), "1.50")) for n in range(lines)]
    rows.append(TableRow(6 + lines, ("Total", None, None, "9.00")))
    return SheetTable(columns=(1, 2, 3, 4), rows=tuple(rows))


def _skipped(result: object) -> WarningDetails | None:
    found = [w for w in result.warnings if w.code == "rows_skipped"]  # type: ignore[attr-defined]
    assert len(found) <= 1
    return found[0].details if found else None


_SHIP_TO = [{"field": "ship_to", "cells": [{"row": 2, "column": "B"}, {"row": 3, "column": "B"}]}]


async def _run(table: SheetTable, **changes: object) -> object:
    # By default the header block is used: PO number (row 1, in mapping()) and ship-to.
    response = FakeResponse(json.dumps(mapping(**{"addresses": _SHIP_TO, **changes})))
    return await extract_spreadsheet(FakeLLMClient([response]), table, SETTINGS)


async def test_an_injected_first_data_row_lists_every_skipped_data_row() -> None:
    table = _po(lines=5)  # data rows 6..10, total at 11
    result = await _run(table, first_data_row=10, footer_rows=[11])
    assert [line.raw_text for line in result.extraction.lines] == ["P4 | Widget 4 | 5 | 1.50"]
    details = _skipped(result)
    assert details is not None
    # Row 5 is a heading, but not the one directly above row 10: listed too.
    assert details.rows == (5, 6, 7, 8, 9, 11)
    assert details.texts == (
        "Item | Description | Qty | Price",
        "P0 | Widget 0 | 1 | 1.50",
        "P1 | Widget 1 | 2 | 1.50",
        "P2 | Widget 2 | 3 | 1.50",
        "P3 | Widget 3 | 4 | 1.50",
        "Total | 9.00",
    )
    # The header block the mapping uses is not listed.
    assert not {1, 2, 3} & set(details.rows)


async def test_a_real_footer_total_lists_its_row() -> None:
    result = await _run(_po(lines=2), first_data_row=6, footer_rows=[8])
    details = _skipped(result)
    assert details is not None
    assert (details.rows, details.texts) == ((8,), ("Total | 9.00",))


async def test_no_skipped_rows_no_warning() -> None:
    result = await _run(_po(lines=2), first_data_row=6, footer_rows=[])
    assert _skipped(result) is None


async def test_empty_rows_between_heading_and_data_give_no_warning() -> None:
    table = _po(lines=2)
    shifted = SheetTable(
        columns=table.columns,
        rows=(*table.rows[:4], *(TableRow(r.number + 3, r.cells) for r in table.rows[4:])),
    )  # rows 6..8 are empty (not in the table); data starts at 9
    result = await _run(shifted, first_data_row=9, footer_rows=[])
    assert _skipped(result) is None


async def test_skipped_rows_are_bounded_by_the_table() -> None:
    rows = [TableRow(1, ("Item", "Description", "Qty", "Price"))]
    rows += [TableRow(n, (f"P{n}", "w", "1", "1")) for n in range(2, MAX_TABLE_ROWS + 1)]
    table = SheetTable(columns=(1, 2, 3, 4), rows=tuple(rows))
    result = await _run(table, first_data_row=MAX_TABLE_ROWS, footer_rows=[], header_values=[])
    details = _skipped(result)
    assert details is not None
    # Row 1 (a heading, not directly above) and 4..1999; rows 2-3 are used by ship-to.
    assert details.rows == (1, *range(4, MAX_TABLE_ROWS))
    assert len(details.rows) <= MAX_TABLE_ROWS


async def test_skipped_rows_are_logged_as_a_count_only() -> None:
    with structlog.testing.capture_logs() as logs:
        await _run(_po(lines=5), first_data_row=10, footer_rows=[11])
    built = next(e for e in logs if e["event"] == "extraction.spreadsheet_built")
    assert built["skipped_rows"] == 6  # rows 5-9 and the footer
    assert "Widget" not in repr(logs) and "Total" not in repr(logs)


# --- A, tightened: every row above first_data_row is accounted for ----------------------

_TEXT_ONLY_COLUMNS = [
    {"column": "A", "field": "customer_part_number"},
    {"column": "B", "field": "description"},
]


async def test_no_numeric_column_mapped_still_warns_on_every_skipped_data_row() -> None:
    table = _po(lines=5)  # data rows 6..10 (their qty/price are numbers), total at 11
    result = await _run(table, first_data_row=10, footer_rows=[11], columns=_TEXT_ONLY_COLUMNS)
    details = _skipped(result)
    assert details is not None
    # 6..8 are data rows; 9 is not the heading either: it holds numbers ("4", "1.50").
    # Row 5 (a heading, but not directly above row 10) is listed too: nothing above
    # first_data_row disappears silently.
    assert details.rows == (5, 6, 7, 8, 9, 11)


def _normal_sheet() -> SheetTable:
    return SheetTable(
        columns=(1, 2, 3, 4),
        rows=(
            TableRow(1, ("PURCHASE ORDER", None, None, None)),
            TableRow(2, ("PO#: 4500012345", None, None, None)),
            TableRow(3, ("Ship to", "Acme Corp", None, None)),
            TableRow(4, (None, "1 Main St", None, None)),
            TableRow(6, ("Item", "Description", "Qty", "Price")),
            TableRow(7, ("P1", "Widget", "2", "1.50")),
            TableRow(8, ("P2", "Bolt", "3", "0.25")),
        ),
    )


async def test_a_normal_sheet_flags_nothing_in_its_header_block() -> None:
    result = await _run(
        _normal_sheet(),
        first_data_row=7,
        footer_rows=[],
        header_values=[
            {"field": "notes", "row": 1, "column": "A", "value": "PURCHASE ORDER"},
            {"field": "customer_po_number", "row": 2, "column": "A", "value": "4500012345"},
        ],
        addresses=[
            {"field": "ship_to", "cells": [{"row": 3, "column": "B"}, {"row": 4, "column": "B"}]}
        ],
    )
    assert _skipped(result) is None  # header fields used, heading row 6 above row 7


async def test_an_unreferenced_row_above_the_table_is_listed() -> None:
    # The owner's rule is absolute: an unused title or header row is listed, not dropped.
    result = await _run(
        _normal_sheet(),
        first_data_row=7,
        footer_rows=[],
        addresses=[],
        header_values=[
            {"field": "customer_po_number", "row": 2, "column": "A", "value": "4500012345"}
        ],
    )
    details = _skipped(result)
    assert details is not None
    assert details.rows == (1, 3, 4)
    assert details.texts == ("PURCHASE ORDER", "Ship to | Acme Corp", "1 Main St")


async def test_a_row_used_by_header_values_is_not_flagged() -> None:
    result = await _run(
        _normal_sheet(),
        first_data_row=7,
        footer_rows=[],
        addresses=[],
        header_values=[
            {"field": "notes", "row": 1, "column": "A", "value": "PURCHASE ORDER"},
            {"field": "customer_po_number", "row": 2, "column": "A", "value": "4500012345"},
            {"field": "customer_name", "row": 3, "column": "B", "value": "Acme Corp"},
            {"field": "payment_terms", "row": 4, "column": "B", "value": "1 Main St"},
        ],
    )
    assert _skipped(result) is None


async def test_the_heading_must_hold_no_number_when_nothing_numeric_is_mapped() -> None:
    # Row 9 (just above first_data_row) holds numbers: it is a data row, not the heading.
    result = await _run(_po(lines=5), first_data_row=10, footer_rows=[], columns=_TEXT_ONLY_COLUMNS)
    details = _skipped(result)
    assert details is not None and 9 in details.rows

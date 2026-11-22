"""Warnings with details (owner, approved plan item (a)) and skipped rows (owner, M4)."""

import json

import pytest
import structlog

from app.extraction.column_mapping import mapping_settings
from app.extraction.extract import DocumentInput, extract_po
from app.extraction.grounding import ExtractionWarning, WarningDetails
from app.extraction.inputs import parse_document
from app.extraction.inputs.limits import MAX_SHEET_NAME_CHARS, MAX_SHEET_NAMES, MAX_TABLE_ROWS
from app.extraction.inputs.table import SheetTable
from app.extraction.spreadsheet import extract_spreadsheet
from app.llm.fake import FakeLLMClient, FakeResponse
from tests.extraction.inputs.sheets import Sheet, inline, make_xlsx, row, sheet_xml, text_rows
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

"""Spreadsheets through `run_parsers`, `parse_document` and the parsing child (T6)."""

import contextlib

import pytest
import structlog

from app.extraction.inputs import ParsedDocument, Rejection, parse_document, run_parsers
from app.extraction.inputs.errors import InputError
from app.extraction.inputs.isolate import parse_isolated
from tests.extraction.inputs.sheets import (
    HOSTILE_PROLOGUES,
    Sheet,
    inline,
    make_cfb,
    make_xlsx,
    row,
    sheet_xml,
    text_rows,
)

SECRET_CELL = "SECRET-CELL-7a2c"
SECRET_SHEET = "SECRET-SHEET-31"
CSV = f"Item,Qty\n{SECRET_CELL},12\n".encode()


def workbook() -> bytes:
    rows = text_rows([["Item", "Qty"], [SECRET_CELL, "12"]]) + row(
        3, inline("A3", "HIDDEN"), attrs=' hidden="1"'
    )
    return make_xlsx(
        [Sheet("PO", sheet_xml(rows)), Sheet(SECRET_SHEET, sheet_xml(text_rows([["x"]])))]
    )


@pytest.mark.parametrize("data", [CSV, workbook()])
def test_a_child_process_parses_spreadsheets_like_the_parent(data: bytes) -> None:
    isolated = parse_isolated(data)
    assert isolated == parse_document(data)
    assert isolated.table is not None and isolated.text is None


def test_the_xlsx_document() -> None:
    document = parse_isolated(workbook())
    assert document.kind == "xlsx"
    assert document.warnings == ("other_sheets_ignored", "hidden_cells_ignored")
    assert document.table is not None
    assert document.table.other_sheets == (SECRET_SHEET,)
    assert [r.cells for r in document.table.rows] == [("Item", "Qty"), (SECRET_CELL, "12")]


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (make_cfb(["EncryptedPackage"]), "encrypted_spreadsheet"),
        (make_cfb(["Workbook"]), "unsupported_spreadsheet"),
        (
            make_xlsx(content_types=HOSTILE_PROLOGUES["billion_laughs"][0] + "<Types/>"),
            "invalid_file",
        ),
        (b"Item,Qty\n" + b"x,1\n" * 2_001, "too_many_rows"),
        (b"Item,Qty\n\x00,1\n", "unsupported_type"),  # a NUL: not text, so not a CSV
    ],
)
def test_hostile_or_refused_spreadsheets_end_in_a_code_in_the_child(data: bytes, code: str) -> None:
    with pytest.raises(InputError) as caught:
        parse_isolated(data)
    assert caught.value.code == code


def test_a_spreadsheet_never_reaches_extract_po() -> None:
    document = parse_document(CSV)
    with pytest.raises(ValueError, match="extract_spreadsheet"):
        document.to_document_input()


def test_an_unexpected_parser_error_is_invalid_file() -> None:
    sheet = sheet_xml(row(1, inline("A1", "a"))).replace("<sheetData>", "<sheetData><row r='x'/>")
    outcome = run_parsers(make_xlsx([Sheet("PO", sheet)]))
    assert outcome == Rejection(code="invalid_file", kind="xlsx")


@pytest.mark.parametrize("data", [CSV, workbook()])
def test_logs_carry_counts_never_cells_or_sheet_names(data: bytes) -> None:
    with structlog.testing.capture_logs() as logs:
        document = parse_isolated(data)
    (entry,) = logs
    assert entry["event"] == "document.parsed"
    assert document.table is not None
    assert entry["table_rows"] == len(document.table.rows)
    assert entry["table_columns"] == len(document.table.columns)
    assert SECRET_CELL not in repr(logs) and SECRET_SHEET not in repr(logs)
    assert "HIDDEN" not in repr(logs)


def test_a_rejected_spreadsheet_logs_no_content() -> None:
    data = b"Item,Qty\n" + f"{SECRET_CELL},1\n".encode() * 2_001
    with structlog.testing.capture_logs() as logs, contextlib.suppress(InputError):
        parse_document(data)
    assert logs[0]["code"] == "too_many_rows"
    assert SECRET_CELL not in repr(logs)


def test_the_repr_hides_the_table() -> None:
    document = parse_document(workbook())
    assert isinstance(document, ParsedDocument)
    assert SECRET_CELL not in repr(document) and SECRET_SHEET not in repr(document)
    assert document.table is not None
    assert SECRET_CELL not in repr(document.table) and SECRET_SHEET not in repr(document.table)

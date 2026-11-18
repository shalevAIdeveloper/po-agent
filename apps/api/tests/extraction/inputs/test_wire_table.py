"""Wire v2: the spreadsheet table crosses as untrusted data and is re-checked by the parent."""

import json
import struct
from typing import Any

import pytest

from app.extraction.inputs import ParsedDocument, run_parsers
from app.extraction.inputs.limits import (
    MAX_CELL_CHARS,
    MAX_ROWS_READ,
    MAX_SHEET_NAME_CHARS,
    MAX_SHEET_NAMES,
    MAX_TABLE_CHARS,
    MAX_TABLE_COLUMNS,
    MAX_TABLE_ROWS,
)
from app.extraction.inputs.table import SheetTable, TableRow
from app.extraction.inputs.wire import MAX_HEADER_BYTES, WireError, decode, encode
from tests.extraction.inputs.sheets import Sheet, make_xlsx, sheet_xml, text_rows


def frame(header: dict[str, Any]) -> bytes:
    raw = json.dumps(header).encode()
    return struct.pack(">Q", len(raw)) + raw


def table(**changes: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "columns": [1, 3],
        "rows": [[1, ["Item", "Qty"]], [4, ["00123-A", None]]],
        "other_sheets": [],
        "hidden_rows": 0,
        "hidden_columns": 0,
        "formulas_without_value": 0,
    }
    value.update(changes)
    return value


def header(warnings: list[str] | None = None, kind: str = "xlsx", **changes: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "v": 2,
        "status": "ok",
        "kind": kind,
        "text": None,
        "page_count": 1,
        "warnings": warnings or [],
        "claimed_from": None,
        "claimed_date": None,
        "images": [],
        "table": table(),
    }
    value.update(changes)
    return value


def test_a_valid_table_decodes() -> None:
    document = decode(frame(header()))
    assert isinstance(document, ParsedDocument)
    assert document.table == SheetTable(
        columns=(1, 3),
        rows=(TableRow(1, ("Item", "Qty")), TableRow(4, ("00123-A", None))),
    )


@pytest.mark.parametrize(
    "data",
    [
        b"Item,Qty\n00123-A,12\n",
        make_xlsx(
            [
                Sheet("PO", sheet_xml(text_rows([["Item", "Qty"], ["00123-A", "12"]]))),
                Sheet("Other <b>", sheet_xml(text_rows([["x"]]))),
            ]
        ),
    ],
)
def test_spreadsheets_round_trip(data: bytes) -> None:
    outcome = run_parsers(data)
    assert isinstance(outcome, ParsedDocument) and outcome.table is not None
    assert decode(encode(outcome)) == outcome


def test_the_largest_table_fits_in_the_header() -> None:
    # 4-byte characters at MAX_TABLE_CHARS, every cell slot used, the most sheet names.
    char = chr(0x1F4E6)
    per_cell = MAX_TABLE_CHARS // (MAX_TABLE_ROWS * MAX_TABLE_COLUMNS)
    rows = tuple(
        TableRow(number, (char * per_cell,) * MAX_TABLE_COLUMNS)
        for number in range(1, MAX_TABLE_ROWS + 1)
    )
    names = tuple(char * MAX_SHEET_NAME_CHARS for _ in range(MAX_SHEET_NAMES))
    document = ParsedDocument(
        kind="xlsx",
        text=None,
        images=(),
        page_count=1,
        warnings=("other_sheets_ignored",),
        table=SheetTable(
            columns=tuple(range(1, MAX_TABLE_COLUMNS + 1)), rows=rows, other_sheets=names
        ),
    )
    reply = encode(document)
    assert len(reply) - 8 <= MAX_HEADER_BYTES
    assert decode(reply) == document


@pytest.mark.parametrize(
    "bad",
    [
        header(table=None),  # a spreadsheet without its table
        header(kind="eml", text="t"),  # a table on another kind
        header(text="t"),
        header(images=[{"media_type": "image/png", "length": 3}]),
        header(page_count=2),
        header(claimed_from="a@b.example"),
        header(table=[]),
        header(table={**table(), "extra": 1}),
        header(table=table(columns=[])),
        header(table=table(columns=[3, 1])),
        header(table=table(columns=[1, 1])),
        header(table=table(columns=[0, 1])),
        header(table=table(columns=[1, 16_385])),
        header(table=table(columns=[1, True])),
        header(table=table(columns=list(range(1, MAX_TABLE_COLUMNS + 2)))),
        header(table=table(rows=[])),
        header(table=table(rows=[[4, ["a", "b"]], [1, ["c", "d"]]])),
        header(table=table(rows=[[1, ["a", "b"]], [1, ["c", "d"]]])),
        header(table=table(rows=[[0, ["a", "b"]]])),
        header(table=table(rows=[[MAX_ROWS_READ + 1, ["a", "b"]]])),
        header(table=table(rows=[[1, ["a"]]])),  # wrong width
        header(table=table(rows=[[1, [None, None]]])),  # an empty row
        header(table=table(rows=[[1, ["a", None]]])),  # an empty column
        header(table=table(rows=[[1, ["a", ""]]])),
        header(table=table(rows=[[1, ["a", "  "]]])),
        header(table=table(rows=[[1, ["a", "x\x00y"]]])),
        header(table=table(rows=[[1, ["a", "x\r\ny"]]])),
        header(table=table(rows=[[1, ["a", chr(0xD800)]]])),
        header(table=table(rows=[[1, ["a", 5]]])),
        header(table=table(rows=[[1, ["a", "x" * (MAX_CELL_CHARS + 1)]]])),
        header(table=table(rows=[[1, ["a", "b"], "extra"]])),
        header(table=table(rows=[[n, ["a", "b"]] for n in range(1, MAX_TABLE_ROWS + 2)])),
        header(table=table(rows=[[n, ["x" * MAX_CELL_CHARS, "b"]] for n in range(1, 32)])),
        header(table=table(other_sheets=["x"])),  # names without the warning
        header(["other_sheets_ignored"], table=table(other_sheets=["x" * 32])),
        header(["other_sheets_ignored"], table=table(other_sheets=["a\nb"])),
        header(["other_sheets_ignored"], table=table(other_sheets=[" a"])),
        header(["other_sheets_ignored"], table=table(other_sheets=["‮a"])),
        header(["other_sheets_ignored"], table=table(other_sheets=[7])),
        header(["other_sheets_ignored"], table=table(other_sheets=["a"] * 256)),
        header(table=table(hidden_rows=1)),  # a count without its warning
        header(["hidden_cells_ignored"]),  # a warning without its count
        header(table=table(formulas_without_value=2)),
        header(["formula_without_value"]),
        header(table=table(hidden_columns=-1)),
        header(table=table(hidden_rows=1.5)),
        header(["encoding_guessed"]),  # a CSV warning on an XLSX
        header(["macros_ignored"], kind="csv"),
        header(["attachments_ignored"], kind="csv"),
        header(kind="pdf", text="t", table=None, warnings=["macros_ignored"]),
    ],
)
def test_a_table_a_parser_could_not_produce_is_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(WireError):
        decode(frame(bad))


def test_warnings_with_details_are_accepted() -> None:
    document = decode(
        frame(
            header(
                ["other_sheets_ignored", "hidden_cells_ignored", "formula_without_value"],
                table=table(other_sheets=["Sheet 2"], hidden_columns=1, formulas_without_value=3),
            )
        )
    )
    assert isinstance(document, ParsedDocument) and document.table is not None
    assert document.table.other_sheets == ("Sheet 2",)
    # The warning may come without a name (a blank name is dropped by the parser).
    assert isinstance(decode(frame(header(["other_sheets_ignored"]))), ParsedDocument)

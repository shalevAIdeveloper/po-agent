import codecs
import csv
import time

import pytest

from app.extraction.inputs.csvfile import parse_csv
from app.extraction.inputs.errors import InputError
from app.extraction.inputs.limits import (
    MAX_CELL_CHARS,
    MAX_ROWS_READ,
    MAX_TABLE_CHARS,
    MAX_TABLE_COLUMNS,
    MAX_TABLE_ROWS,
)
from app.extraction.inputs.table import SheetTable, clean_cell, clean_sheet_name

PO_CSV = (
    b"PO Number,4500012345\n"
    b"Date,11/16/26\n"
    b"\n"
    b"Line,Item,Description,Qty,Price\n"
    b'1,00123-A,"Widget, blue",12,"$1,234.50"\n'
    b"2,00124,Bolt,3,0.25\n"
)


def table_of(data: bytes) -> SheetTable:
    return parse_csv(data).table


def code_of(data: bytes) -> str:
    with pytest.raises(InputError) as caught:
        parse_csv(data)
    return caught.value.code


def test_a_csv_becomes_a_table_of_non_empty_rows() -> None:
    parsed = parse_csv(PO_CSV)
    table = parsed.table
    assert parsed.warnings == ()
    assert table.columns == (1, 2, 3, 4, 5)
    assert [row.number for row in table.rows] == [1, 2, 4, 5, 6]  # record 3 is blank
    assert table.rows[0].cells == ("PO Number", "4500012345", None, None, None)
    assert table.rows[3].cells == ("1", "00123-A", "Widget, blue", "12", "$1,234.50")


def test_semicolon_and_tab_files() -> None:
    assert table_of(b"Item;Qty\n00123-A;1,5\n").rows[1].cells == ("00123-A", "1,5")
    assert table_of(b"Item\tQty\n00123-A\t12\n").rows[1].cells == ("00123-A", "12")


def test_a_quoted_field_keeps_its_line_break_and_the_record_number_counts_records() -> None:
    table = table_of(b'Item,Note\n00123-A,"line one\r\nline two"\n00124,x\n')
    assert table.rows[1].cells == ("00123-A", "line one\nline two")
    assert [row.number for row in table.rows] == [1, 2, 3]


def test_empty_columns_are_dropped_and_numbers_kept() -> None:
    table = table_of(b"Item,,Qty\n00123-A,,12\n")
    assert table.columns == (1, 3)
    assert table.rows[1].cells == ("00123-A", "12")


def test_formula_text_is_kept_as_text() -> None:
    table = table_of(b'Item,Qty\n=HYPERLINK("http://x.example"),=1+1\n')
    assert table.rows[1].cells == ('=HYPERLINK("http://x.example")', "=1+1")


def test_whitespace_only_cells_are_empty_and_text_is_kept_as_written() -> None:
    table = table_of(b"Item,Qty\n  00123-A ,   \nx,1\n")
    assert table.rows[1].cells == ("  00123-A ", None)


def test_encodings_and_the_guess_warning() -> None:
    utf16 = codecs.BOM_UTF16_LE + "Item,Qty\nCafé,12\n".encode("utf-16-le")
    assert parse_csv(utf16).table.rows[1].cells == ("Café", "12")
    assert parse_csv(utf16).warnings == ()
    bom = codecs.BOM_UTF8 + "Item,Qty\nCafé,12\n".encode()
    assert parse_csv(bom).table.rows[0].cells == ("Item", "Qty")
    guessed = parse_csv("Item,Qty\nCafé €,12\n".encode("cp1252"))
    assert guessed.table.rows[1].cells == ("Café €", "12")
    assert guessed.warnings == ("encoding_guessed",)


def test_a_nul_is_refused() -> None:
    assert code_of(b"Item,Qty\n00123-A,1\0 2\n") == "invalid_file"


def test_more_than_the_row_cap_is_refused() -> None:
    header = b"Item,Qty\n"
    assert len(table_of(header + b"x,1\n" * (MAX_TABLE_ROWS - 1)).rows) == MAX_TABLE_ROWS
    assert code_of(header + b"x,1\n" * MAX_TABLE_ROWS) == "too_many_rows"


def test_too_many_records_read_is_refused_even_if_blank() -> None:
    data = b"Item,Qty\nx,1\n" + b"\n" * MAX_ROWS_READ + b"y,2\n"
    assert code_of(data) == "too_many_rows"


def test_more_than_the_column_cap_is_refused() -> None:
    ok = ",".join(["c"] * MAX_TABLE_COLUMNS).encode() + b"\n"
    assert len(table_of(ok * 2).columns) == MAX_TABLE_COLUMNS
    wide = ",".join(["c"] * (MAX_TABLE_COLUMNS + 1)).encode() + b"\n"
    assert code_of(wide * 2) == "document_too_large"


def test_a_giant_field_is_refused_and_the_limit_restored() -> None:
    before = csv.field_size_limit()
    at_limit = b"a,b\n" + b"x" * MAX_CELL_CHARS + b",1\n"
    assert table_of(at_limit).rows[1].cells[0] == "x" * MAX_CELL_CHARS
    over = b"a,b\n" + b"x" * (MAX_CELL_CHARS + 1) + b",1\n"
    assert code_of(over) == "document_too_large"
    # An unclosed quote turns the rest of the file into one field.
    unbalanced = b'a,b\nc,d\n"' + b"x," * MAX_CELL_CHARS + b"\n"
    assert code_of(unbalanced) == "document_too_large"
    assert csv.field_size_limit() == before


def test_the_table_char_cap() -> None:
    cell = "x" * 5_000
    rows = (f"{cell},{cell}\n" * (MAX_TABLE_CHARS // 10_000 + 1)).encode()
    assert code_of(rows) == "document_too_large"


def test_a_comma_flood_is_bounded_in_time() -> None:
    start = time.perf_counter()
    assert table_of(b"a,b\n" + b"," * 5_000 + b"x\n").columns == (1, 2, 5_001)
    # A column past Excel's last one (XFD) is more than a purchase order needs.
    assert code_of(b"a,b\n" + b"," * 5_000_000 + b"x\n") == "document_too_large"
    assert time.perf_counter() - start < 10


def test_an_empty_csv_is_empty() -> None:
    assert code_of(b",,\n,  ,\n") == "empty_document"
    assert code_of(b'"",""\n"",""\n') == "empty_document"


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("a\r\nb\rc", "a\nb\nc"),
        ("PO\x00 1\x07\x1b\x9f", "PO 1"),
        ("tab\there", "tab\there"),
        ("x" + chr(0xD800), "x?"),
        (" \n\t ", None),
        ("", None),
    ],
)
def test_clean_cell(raw: str, clean: str | None) -> None:
    assert clean_cell(raw) == clean


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("Sheet 2", "Sheet 2"),
        ("a\nb\x00c", "a b c"),
        ("x" * 40, "x" * 31),
        ("  ", None),
        # Format characters (bidi overrides, zero-width) can't reorder or hide a label.
        ("‮evil​", "evil"),
    ],
)
def test_clean_sheet_name(raw: str, clean: str | None) -> None:
    assert clean_sheet_name(raw) == clean

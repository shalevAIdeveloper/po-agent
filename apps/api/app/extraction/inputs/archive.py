"""ZIP packages (XLSX): caps checked on the central directory before anything is inflated.

- The end-of-central-directory record is read first: ZIP64, multi-disk archives, more than
  MAX_ZIP_ENTRIES entries or a central directory over MAX_ZIP_DIRECTORY_BYTES are refused
  before `zipfile` builds its entry list, so a 20 MB file of tiny entries can't cost
  hundreds of MB in the worker or the API (which runs detection on upload, PR 13).
- Then every entry: no encryption, only stored or deflated data, no duplicate names, at
  most MAX_ZIP_ENTRY_BYTES each and MAX_ZIP_TOTAL_BYTES together, as declared.
  `zipfile` never inflates past an entry's declared size (and checks the CRC), so a
  declared size that lies can't produce more bytes than the caps allow.
- `[Content_Types].xml` is parsed with defusedxml, DTDs forbidden: no entity expansion,
  no external reference is ever fetched.

Structural problems are `invalid_file`; sizes over the caps are `document_too_large`.
"""

import io
import struct
import zipfile
from dataclasses import dataclass
from typing import Final, cast
from xml.etree.ElementTree import Element

import defusedxml.ElementTree as SafeET

from app.extraction.inputs.errors import InputError
from app.extraction.inputs.limits import (
    MAX_CONTENT_TYPES_BYTES,
    MAX_ZIP_DIRECTORY_BYTES,
    MAX_ZIP_ENTRIES,
    MAX_ZIP_ENTRY_BYTES,
    MAX_ZIP_TOTAL_BYTES,
)

LOCAL_HEADER: Final = b"PK\x03\x04"
EMPTY_ARCHIVE: Final = b"PK\x05\x06"
_EOCD: Final = struct.Struct("<4sHHHHIIH")
_ZIP64_LOCATOR: Final = b"PK\x06\x07"
_ZIP64_LOCATOR_SIZE: Final = 20
_MAX_COMMENT: Final = 0xFFFF
_ENCRYPTED_FLAG: Final = 0x1
_METHODS: Final = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})

CONTENT_TYPES: Final = "[Content_Types].xml"
_CT_NS: Final = "{http://schemas.openxmlformats.org/package/2006/content-types}"


def is_zip(data: bytes) -> bool:
    return data.startswith((LOCAL_HEADER, EMPTY_ARCHIVE))


def _check_end_record(data: bytes) -> None:
    start = max(0, len(data) - _EOCD.size - _MAX_COMMENT)
    position = data.rfind(b"PK\x05\x06", start)
    if position < 0 or position + _EOCD.size > len(data):
        raise InputError("invalid_file")
    _sig, disk, cd_disk, disk_entries, entries, cd_size, cd_offset, _comment = _EOCD.unpack_from(
        data, position
    )
    if data[max(0, position - _ZIP64_LOCATOR_SIZE) : position].startswith(_ZIP64_LOCATOR):
        raise InputError("invalid_file")  # ZIP64: never needed for a 20 MB workbook
    if disk != 0 or cd_disk != 0 or disk_entries != entries:
        raise InputError("invalid_file")
    if entries > MAX_ZIP_ENTRIES or cd_size > MAX_ZIP_DIRECTORY_BYTES:
        raise InputError("invalid_file")
    if cd_offset + cd_size > position:
        raise InputError("invalid_file")


def open_archive(data: bytes) -> zipfile.ZipFile:
    """The archive, once its central directory passes the caps. Raises `InputError`."""
    _check_end_record(data)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError, OSError, EOFError, struct.error):
        raise InputError("invalid_file") from None
    infos = archive.infolist()
    names = [info.filename for info in infos]
    if len(infos) > MAX_ZIP_ENTRIES or len(set(names)) != len(names):
        raise InputError("invalid_file")
    total = 0
    for info in infos:
        if info.flag_bits & _ENCRYPTED_FLAG or info.compress_type not in _METHODS:
            raise InputError("invalid_file")
        if info.file_size > MAX_ZIP_ENTRY_BYTES:
            raise InputError("document_too_large")
        total += info.file_size
    if total > MAX_ZIP_TOTAL_BYTES:
        raise InputError("document_too_large")
    return archive


def read_entry(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > limit:
        raise InputError("invalid_file")
    return archive.read(info)


def parse_xml(data: bytes) -> Element:
    """defusedxml, DTDs forbidden: no entities, nothing external ever fetched."""
    return cast(Element, SafeET.fromstring(data, forbid_dtd=True))


def refuse_dtds(archive: zipfile.ZipFile) -> None:
    """No XML part may declare a DTD (OOXML never does).

    openpyxl's defusedxml calls forbid entities and external references but allow a
    DTD; this closes that gap for every part it might read. Only each part's prolog is
    parsed (up to its root element), streamed. defusedxml raises `DTDForbidden`.
    """
    for info in archive.infolist():
        if info.filename.lower().endswith((".xml", ".rels", ".vml")):
            with archive.open(info) as source:
                for _event in SafeET.iterparse(source, events=("start",), forbid_dtd=True):
                    break


@dataclass(frozen=True, slots=True)
class ContentTypes:
    overrides: dict[str, str]  # part name ("/xl/workbook.xml") -> content type
    defaults: dict[str, str]  # extension (lower case) -> content type

    def of(self, part_name: str) -> str | None:
        if part_name in self.overrides:
            return self.overrides[part_name]
        extension = part_name.rpartition(".")[2].lower()
        return self.defaults.get(extension)

    def parts_of_type(self, content_type: str) -> list[str]:
        return [name for name, kind in self.overrides.items() if kind == content_type]


def read_content_types(archive: zipfile.ZipFile) -> ContentTypes:
    root = parse_xml(read_entry(archive, CONTENT_TYPES, MAX_CONTENT_TYPES_BYTES))
    overrides: dict[str, str] = {}
    defaults: dict[str, str] = {}
    for element in root:
        if element.tag == f"{_CT_NS}Override":
            name, kind = element.get("PartName"), element.get("ContentType")
            if name is not None and kind is not None:
                overrides[name] = kind
        elif element.tag == f"{_CT_NS}Default":
            extension, kind = element.get("Extension"), element.get("ContentType")
            if extension is not None and kind is not None:
                defaults[extension.lower()] = kind
    return ContentTypes(overrides=overrides, defaults=defaults)

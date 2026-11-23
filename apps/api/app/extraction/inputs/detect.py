"""File type from the bytes only: the filename and the declared Content-Type are never used.

Order: PNG, JPEG, PDF, ZIP, CFB, EML, a late PDF header, CSV. Anything else (HEIC, GIF,
WebP, ...) is `unsupported_type` (Q2).

- ZIP: XLSX (and XLSM) only when `[Content_Types].xml` (read with defusedxml) gives
  `xl/workbook.xml`, and nothing else, a workbook content type (`workbook_content_type`).
  `.xlsb` (`xl/workbook.bin`), `.ods` (its ODF `mimetype`) and templates are
  `unsupported_spreadsheet`; any other ZIP (`.docx`, a plain archive) is
  `unsupported_type`. The central directory is checked against the
  caps before any entry is read (`archive.py`).
- CFB (legacy Office): a `Workbook`/`Book` stream is `.xls` (`unsupported_spreadsheet`);
  an `EncryptedPackage` stream is a password-protected workbook (`encrypted_spreadsheet`;
  an encrypted Word file looks the same). Any other CFB is `unsupported_type`.
- CSV is tried last, with our own linear sniffer (`csvfile.py`).
"""

import re
from typing import Final, Literal

from app.extraction.inputs import archive, cfb
from app.extraction.inputs.csvfile import looks_like_csv
from app.extraction.inputs.errors import InputError

DocumentKind = Literal["pdf", "png", "jpeg", "eml", "xlsx", "csv"]
SPREADSHEET_KINDS: Final = frozenset({"xlsx", "csv"})

_PNG: Final = b"\x89PNG\r\n\x1a\n"
_JPEG: Final = b"\xff\xd8\xff"
_PDF: Final = b"%PDF-"
# Readers accept a few junk bytes before `%PDF-` (the PDF spec's implementation note).
PDF_HEADER_WINDOW: Final = 1024

# An email starts with RFC 5322 header fields up to the first blank line. Long `Received` /
# ARC chains (Microsoft 365) can pass 16 KB (`qa`).
EML_HEADER_WINDOW: Final = 64 * 1024
# Mail-client exports may start with an mbox separator line ("From - Mon Nov 16 ...").
_MBOX_FROM: Final = b"From "
_FIELD_RE: Final = re.compile(rb"^[!-9;-~]{1,76}:")
_CONTINUATION_RE: Final = re.compile(rb"^[ \t]")
_EMAIL_FIELDS: Final = frozenset(
    {
        b"from",
        b"to",
        b"cc",
        b"subject",
        b"date",
        b"message-id",
        b"mime-version",
        b"received",
        b"return-path",
        b"reply-to",
        b"content-type",
        b"sender",
    }
)
# A header block needs `From` and at least this many known email fields, so a CSV or a
# text file that starts with "Item: ..." isn't taken for an email.
MIN_EMAIL_FIELDS: Final = 2

WORKBOOK_PART: Final = "/xl/workbook.xml"
XLSX_MAIN: Final = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
XLSM_MAIN: Final = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"
_TEMPLATE_MAIN: Final = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml",
        "application/vnd.ms-excel.template.macroEnabled.main+xml",
    }
)
_ODF_MIMETYPE: Final = "mimetype"
_ODF_SPREADSHEET: Final = b"application/vnd.oasis.opendocument.spreadsheet"
_MAX_MIMETYPE_BYTES: Final = 256
_XLS_STREAMS: Final = frozenset({"Workbook", "Book"})
_ENCRYPTED_STREAM: Final = "EncryptedPackage"


def _looks_like_email(data: bytes) -> bool:
    head = data[:EML_HEADER_WINDOW]
    lines = head.replace(b"\r\n", b"\n").split(b"\n")
    if lines[0].startswith(_MBOX_FROM):
        lines = lines[1:]
    names: set[bytes] = set()
    for line in lines:
        if line == b"":
            break
        match = _FIELD_RE.match(line)
        if match:
            names.add(match.group(0)[:-1].lower())
        elif not (names and _CONTINUATION_RE.match(line)):
            return False
    else:
        return False  # no blank line within the window: no body we can find
    known = names & _EMAIL_FIELDS
    return b"from" in known and len(known) >= MIN_EMAIL_FIELDS


_WORKBOOK_TYPES: Final = frozenset({XLSX_MAIN, XLSM_MAIN, *_TEMPLATE_MAIN})


def workbook_content_type(types: archive.ContentTypes) -> str | None:
    """The workbook part's content type, or None if the package declares no workbook.

    openpyxl reads the first override with a workbook content type, wherever it points,
    or falls back to a `Default` one. So exactly one override may have a workbook type,
    it must be `/xl/workbook.xml` (the part detection and our checks read), and no
    `Default` may have one; anything else is `invalid_file` (`sec` N1: a decoy workbook
    part).
    """
    parts = [name for name, kind in types.overrides.items() if kind in _WORKBOOK_TYPES]
    if any(kind in _WORKBOOK_TYPES for kind in types.defaults.values()):
        raise InputError("invalid_file")
    if not parts:
        return types.of(WORKBOOK_PART)
    if parts != [WORKBOOK_PART]:
        raise InputError("invalid_file")
    return types.overrides[WORKBOOK_PART]


def _zip_kind(data: bytes) -> DocumentKind:
    try:
        with archive.open_archive(data) as package:
            names = set(package.namelist())
            if "xl/workbook.bin" in names:
                raise InputError("unsupported_spreadsheet")  # .xlsb
            if _ODF_MIMETYPE in names:
                mimetype = archive.read_entry(package, _ODF_MIMETYPE, _MAX_MIMETYPE_BYTES)
                if mimetype.strip().startswith(_ODF_SPREADSHEET):
                    raise InputError("unsupported_spreadsheet")  # .ods / .ots
                raise InputError("unsupported_type")
            if archive.CONTENT_TYPES not in names or WORKBOOK_PART[1:] not in names:
                raise InputError("unsupported_type")
            content_type = workbook_content_type(archive.read_content_types(package))
    except InputError:
        raise
    except Exception:
        # Malformed XML, an entity or a DTD (defusedxml), a damaged entry: never the
        # message, which can quote the file.
        raise InputError("invalid_file") from None
    if content_type in (XLSX_MAIN, XLSM_MAIN):
        return "xlsx"
    if content_type in _TEMPLATE_MAIN:
        raise InputError("unsupported_spreadsheet")
    raise InputError("unsupported_type")


def _cfb_code(data: bytes) -> InputError:
    names = cfb.stream_names(data)
    if names & _XLS_STREAMS:
        return InputError("unsupported_spreadsheet")
    if _ENCRYPTED_STREAM in names:
        return InputError("encrypted_spreadsheet")
    return InputError("unsupported_type")


def detect_kind(data: bytes) -> DocumentKind:
    if data.startswith(_PNG):
        return "png"
    if data.startswith(_JPEG):
        return "jpeg"
    if data.startswith(_PDF):
        return "pdf"
    if archive.is_zip(data):
        return _zip_kind(data)
    if data.startswith(cfb.MAGIC):
        raise _cfb_code(data)
    if _looks_like_email(data):
        return "eml"
    if _PDF in data[:PDF_HEADER_WINDOW]:
        return "pdf"
    if looks_like_csv(data):
        return "csv"
    raise InputError("unsupported_type")

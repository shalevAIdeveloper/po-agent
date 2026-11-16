"""File type from the bytes only: the filename and the declared Content-Type are never used.

Spreadsheets (XLSX/CSV) come with PR 5; until then they are `unsupported_type`, like HEIC,
GIF, WebP and anything else we don't recognise (Q2).
"""

import re
from typing import Final, Literal

from app.extraction.inputs.errors import InputError

DocumentKind = Literal["pdf", "png", "jpeg", "eml"]

_PNG: Final = b"\x89PNG\r\n\x1a\n"
_JPEG: Final = b"\xff\xd8\xff"
_PDF: Final = b"%PDF-"
# Readers accept a few junk bytes before `%PDF-` (the PDF spec's implementation note).
PDF_HEADER_WINDOW: Final = 1024

# An email starts with RFC 5322 header fields up to the first blank line.
EML_HEADER_WINDOW: Final = 16 * 1024
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


def _looks_like_email(data: bytes) -> bool:
    head = data[:EML_HEADER_WINDOW]
    lines = head.replace(b"\r\n", b"\n").split(b"\n")
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


def detect_kind(data: bytes) -> DocumentKind:
    if data.startswith(_PNG):
        return "png"
    if data.startswith(_JPEG):
        return "jpeg"
    if data.startswith(_PDF):
        return "pdf"
    if _looks_like_email(data):
        return "eml"
    if _PDF in data[:PDF_HEADER_WINDOW]:
        return "pdf"
    raise InputError("unsupported_type")

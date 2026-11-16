"""The parsing child's reply: a JSON header plus raw image bytes. Never pickle (`sec` M2).

Frame: 8-byte big-endian header length, the UTF-8 JSON header, then each image's bytes in
order. The parent treats the reply as untrusted (the child parsed a hostile file and may
be compromised): `decode` checks every field against literals, types and the same caps
and sanitizing the parsers apply (text, claimed From/Date), and anything off is
`parse_failed`.
"""

import json
import re
import struct
from datetime import datetime
from typing import Any, Final, get_args

from app.extraction.inputs import ParsedDocument, Rejection, clean_text
from app.extraction.inputs.detect import DocumentKind
from app.extraction.inputs.email import acceptable_address, acceptable_date
from app.extraction.inputs.errors import InputErrorCode, InputWarningCode
from app.extraction.inputs.limits import (
    MAX_PDF_PAGES,
    MAX_TEXT_CHARS,
    MAX_TOTAL_IMAGE_BYTES,
)
from app.llm.types import ImageMediaType, ImagePart

VERSION: Final = 1
_LENGTH: Final = struct.Struct(">Q")
# UTF-8 JSON: at most 4 bytes per character, 6 for a control character escaped as
# `\u00XX` (`sec` delta L-4), plus the small fields.
MAX_HEADER_BYTES: Final = 6 * MAX_TEXT_CHARS + 64 * 1024
# The most the parent ever reads from the child.
MAX_REPLY_BYTES: Final = _LENGTH.size + MAX_HEADER_BYTES + MAX_TOTAL_IMAGE_BYTES
_MAX_CLAIMED_CHARS: Final = 254
_ERROR_TYPE_RE: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")

_KINDS: Final = frozenset(get_args(DocumentKind))
_CODES: Final = frozenset(get_args(InputErrorCode))
_WARNINGS: Final = frozenset(get_args(InputWarningCode))
_MEDIA_TYPES: Final = frozenset({"image/png", "image/jpeg"})


class WireError(Exception):
    """A malformed reply: the caller turns it into `parse_failed`."""


def encode(outcome: ParsedDocument | Rejection) -> bytes:
    if isinstance(outcome, Rejection):
        header: dict[str, Any] = {
            "v": VERSION,
            "status": "rejected",
            "code": outcome.code,
            "kind": outcome.kind,
            "error_type": outcome.error_type,
        }
        images: tuple[ImagePart, ...] = ()
    else:
        images = outcome.images
        header = {
            "v": VERSION,
            "status": "ok",
            "kind": outcome.kind,
            "text": outcome.text,
            "page_count": outcome.page_count,
            "warnings": list(outcome.warnings),
            "claimed_from": outcome.claimed_from,
            "claimed_date": None
            if outcome.claimed_date is None
            else outcome.claimed_date.isoformat(),
            "images": [{"media_type": i.media_type, "length": len(i.data)} for i in images],
        }
    raw = json.dumps(header, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return _LENGTH.pack(len(raw)) + raw + b"".join(image.data for image in images)


def _reject_constant(_value: str) -> None:
    raise WireError("non-finite number")


def _expect(condition: bool) -> None:
    if not condition:
        raise WireError("bad reply")


def _member(value: object, allowed: frozenset[str]) -> bool:
    # isinstance first: an unhashable JSON value would make `in` raise (`sec` delta L-1).
    return isinstance(value, str) and value in allowed


def _optional_str(value: object, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise WireError("bad string")
    return value


def _decode_text(value: object) -> str | None:
    # The parent re-applies what the parsers guarantee (`sec` delta L-2): JSON `\ud800`
    # escapes or `\u0000` would otherwise get past a compromised child.
    text = _optional_str(value, MAX_TEXT_CHARS)
    _expect(text is None or clean_text(text) == text)  # also refuses ""
    return text


def _decode_claimed_from(value: object) -> str | None:
    claimed_from = _optional_str(value, _MAX_CLAIMED_CHARS)
    _expect(claimed_from is None or acceptable_address(claimed_from))
    return claimed_from


def _decode_claimed_date(value: object) -> datetime | None:
    raw = _optional_str(value, 64)
    if raw is None:
        return None
    try:
        claimed_date = datetime.fromisoformat(raw)
    except ValueError:
        raise WireError("bad date") from None
    _expect(acceptable_date(claimed_date))
    return claimed_date


def _decode_rejection(header: dict[str, Any]) -> Rejection:
    _expect(set(header) == {"v", "status", "code", "kind", "error_type"})
    code, kind, error_type = header["code"], header["kind"], header["error_type"]
    _expect(_member(code, _CODES))
    _expect(kind is None or _member(kind, _KINDS))
    _expect(
        error_type is None
        or (isinstance(error_type, str) and _ERROR_TYPE_RE.fullmatch(error_type) is not None)
    )
    return Rejection(code=code, kind=kind, error_type=error_type)


def _decode_images(specs: object, body: bytes) -> tuple[ImagePart, ...]:
    if not isinstance(specs, list) or len(specs) > MAX_PDF_PAGES:
        raise WireError("bad image list")
    _expect(len(body) <= MAX_TOTAL_IMAGE_BYTES)  # `sec` delta L-3
    images: list[ImagePart] = []
    offset = 0
    for spec in specs:
        _expect(isinstance(spec, dict) and set(spec) == {"media_type", "length"})
        media_type, length = spec["media_type"], spec["length"]
        _expect(_member(media_type, _MEDIA_TYPES))
        _expect(type(length) is int and 0 < length <= len(body) - offset)
        media: ImageMediaType = media_type
        images.append(ImagePart(data=body[offset : offset + length], media_type=media))
        offset += length
    _expect(offset == len(body))
    return tuple(images)


def _decode_document(header: dict[str, Any], body: bytes) -> ParsedDocument:
    keys = {"v", "status", "kind", "text", "page_count", "warnings"}
    _expect(set(header) == keys | {"claimed_from", "claimed_date", "images"})
    kind, page_count, warnings = header["kind"], header["page_count"], header["warnings"]
    _expect(_member(kind, _KINDS))
    _expect(type(page_count) is int and 1 <= page_count <= MAX_PDF_PAGES)
    _expect(isinstance(warnings, list) and all(_member(w, _WARNINGS) for w in warnings))
    _expect(len(warnings) == len(set(warnings)))
    text = _decode_text(header["text"])
    claimed_from = _decode_claimed_from(header["claimed_from"])
    claimed_date = _decode_claimed_date(header["claimed_date"])
    images = _decode_images(header["images"], body)
    _expect(text is not None or bool(images))
    return ParsedDocument(
        kind=kind,
        text=text,
        images=images,
        page_count=page_count,
        warnings=tuple(warnings),
        claimed_from=claimed_from,
        claimed_date=claimed_date,
    )


def decode(reply: bytes) -> ParsedDocument | Rejection:
    """Raises `WireError` on anything but a well-formed reply within the caps."""
    _expect(_LENGTH.size <= len(reply) <= MAX_REPLY_BYTES)
    (length,) = _LENGTH.unpack_from(reply)
    _expect(0 < length <= MAX_HEADER_BYTES and _LENGTH.size + length <= len(reply))
    raw = reply[_LENGTH.size : _LENGTH.size + length]
    body = reply[_LENGTH.size + length :]
    try:
        header = json.loads(raw.decode("utf-8", errors="strict"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise WireError("bad header") from None
    _expect(isinstance(header, dict) and header.get("v") == VERSION)
    status = header.get("status")
    if status == "rejected":
        _expect(not body)
        return _decode_rejection(header)
    _expect(status == "ok")
    return _decode_document(header, body)

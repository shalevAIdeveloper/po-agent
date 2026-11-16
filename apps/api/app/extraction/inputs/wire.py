"""The parsing child's reply: a JSON header plus raw image bytes. Never pickle (`sec` M2).

Frame: 8-byte big-endian header length, the UTF-8 JSON header, then each image's bytes in
order. The parent treats the reply as untrusted (the child parsed a hostile file and may
be compromised): `decode` checks every field against literals, types and the same caps
the parsers use, and anything off is `parse_failed`.
"""

import json
import re
import struct
from datetime import datetime
from typing import Any, Final, get_args

from app.extraction.inputs import ParsedDocument, Rejection
from app.extraction.inputs.detect import DocumentKind
from app.extraction.inputs.errors import InputErrorCode, InputWarningCode
from app.extraction.inputs.limits import (
    MAX_PDF_PAGES,
    MAX_TEXT_CHARS,
    MAX_TOTAL_IMAGE_BYTES,
)
from app.llm.types import ImageMediaType, ImagePart

VERSION: Final = 1
_LENGTH: Final = struct.Struct(">Q")
MAX_HEADER_BYTES: Final = 4 * MAX_TEXT_CHARS + 64 * 1024  # UTF-8 text + small fields
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
    raw = json.dumps(header, ensure_ascii=True, allow_nan=False).encode("ascii")
    return _LENGTH.pack(len(raw)) + raw + b"".join(image.data for image in images)


def _reject_constant(_value: str) -> None:
    raise WireError("non-finite number")


def _expect(condition: bool) -> None:
    if not condition:
        raise WireError("bad reply")


def _optional_str(value: object, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise WireError("bad string")
    return value


def _decode_rejection(header: dict[str, Any]) -> Rejection:
    _expect(set(header) == {"v", "status", "code", "kind", "error_type"})
    code, kind, error_type = header["code"], header["kind"], header["error_type"]
    _expect(code in _CODES)
    _expect(kind is None or kind in _KINDS)
    _expect(
        error_type is None
        or (isinstance(error_type, str) and _ERROR_TYPE_RE.fullmatch(error_type) is not None)
    )
    return Rejection(code=code, kind=kind, error_type=error_type)


def _decode_images(specs: object, body: bytes) -> tuple[ImagePart, ...]:
    if not isinstance(specs, list) or len(specs) > MAX_PDF_PAGES:
        raise WireError("bad image list")
    images: list[ImagePart] = []
    offset = 0
    for spec in specs:
        _expect(isinstance(spec, dict) and set(spec) == {"media_type", "length"})
        media_type, length = spec["media_type"], spec["length"]
        _expect(media_type in _MEDIA_TYPES)
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
    _expect(kind in _KINDS)
    _expect(type(page_count) is int and 1 <= page_count <= MAX_PDF_PAGES)
    _expect(isinstance(warnings, list) and all(w in _WARNINGS for w in warnings))
    _expect(len(warnings) == len(set(warnings)))
    text = _optional_str(header["text"], MAX_TEXT_CHARS)
    claimed_from = _optional_str(header["claimed_from"], _MAX_CLAIMED_CHARS)
    claimed_date_raw = _optional_str(header["claimed_date"], 64)
    claimed_date = None
    if claimed_date_raw is not None:
        try:
            claimed_date = datetime.fromisoformat(claimed_date_raw)
        except ValueError:
            raise WireError("bad date") from None
        _expect(claimed_date.tzinfo is not None)
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
        header = json.loads(raw.decode("ascii"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise WireError("bad header") from None
    _expect(isinstance(header, dict) and header.get("v") == VERSION)
    status = header.get("status")
    if status == "rejected":
        _expect(not body)
        return _decode_rejection(header)
    _expect(status == "ok")
    return _decode_document(header, body)

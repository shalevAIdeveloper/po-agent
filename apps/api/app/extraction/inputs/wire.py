"""The parsing child's reply: a JSON header plus raw image bytes. Never pickle (`sec` M2).

Frame: 8-byte big-endian header length, the UTF-8 JSON header, then each image's bytes in
order. The parent treats the reply as untrusted (the child parsed a hostile file and may
be compromised): `decode` checks every field against literals, types and the same caps
and sanitizing the parsers apply (text, claimed From/Date), and anything off is
`parse_failed`.

Version 2 (M1 PR 5) adds the spreadsheet `table`: re-checked like everything else (the
row, column, cell and table caps, increasing row and column numbers, cleaned cells,
sheet names cleaned and capped, counts consistent with the warnings, warnings allowed
for the kind, a table exactly for the spreadsheet kinds).
"""

import itertools
import json
import re
import struct
from datetime import datetime
from typing import Any, Final, get_args

from app.extraction.inputs import ParsedDocument, Rejection, clean_text
from app.extraction.inputs.detect import SPREADSHEET_KINDS, DocumentKind
from app.extraction.inputs.email import acceptable_address, acceptable_date
from app.extraction.inputs.errors import InputErrorCode, InputWarningCode
from app.extraction.inputs.limits import (
    MAX_CELL_CHARS,
    MAX_PDF_PAGES,
    MAX_ROWS_READ,
    MAX_SHEET_COLUMN,
    MAX_SHEET_NAME_CHARS,
    MAX_SHEET_NAMES,
    MAX_TABLE_CHARS,
    MAX_TABLE_COLUMNS,
    MAX_TABLE_ROWS,
    MAX_TEXT_CHARS,
    MAX_TOTAL_IMAGE_BYTES,
)
from app.extraction.inputs.table import SheetTable, TableRow, clean_cell, clean_sheet_name
from app.llm.types import ImageMediaType, ImagePart

VERSION: Final = 2
_LENGTH: Final = struct.Struct(">Q")
# UTF-8 JSON: at most 4 bytes per character, 6 for a control character escaped as
# `\u00XX` (`sec` delta L-4), plus the small fields. A table adds up to 8 bytes of
# syntax per cell (`null, `) and its sheet names.
_CELL_SYNTAX_BYTES: Final = 8
MAX_HEADER_BYTES: Final = (
    6 * max(MAX_TEXT_CHARS, MAX_TABLE_CHARS)
    + _CELL_SYNTAX_BYTES * MAX_TABLE_ROWS * (MAX_TABLE_COLUMNS + 2)
    + MAX_SHEET_NAMES * (6 * MAX_SHEET_NAME_CHARS + 4)
    + 64 * 1024
)
# The most the parent ever reads from the child.
MAX_REPLY_BYTES: Final = _LENGTH.size + MAX_HEADER_BYTES + MAX_TOTAL_IMAGE_BYTES
_MAX_CLAIMED_CHARS: Final = 254
_ERROR_TYPE_RE: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")

_KINDS: Final = frozenset(get_args(DocumentKind))
_CODES: Final = frozenset(get_args(InputErrorCode))
_TABLE_KEYS: Final = frozenset(
    {"columns", "rows", "other_sheets", "hidden_rows", "hidden_columns", "formulas_without_value"}
)
# Which warnings each kind's parser can produce.
_KIND_WARNINGS: Final[dict[str, frozenset[str]]] = {
    "pdf": frozenset(),
    "png": frozenset(),
    "jpeg": frozenset(),
    "eml": frozenset({"attachments_ignored", "alternative_parts_differ", "hidden_html_text"}),
    "xlsx": frozenset(
        {
            "other_sheets_ignored",
            "hidden_sheets_ignored",
            "hidden_cells_ignored",
            "formula_without_value",
            "macros_ignored",
        }
    ),
    "csv": frozenset({"encoding_guessed"}),
}
assert set().union(*_KIND_WARNINGS.values()) == set(get_args(InputWarningCode))  # noqa: S101
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
            "table": None if outcome.table is None else _encode_table(outcome.table),
        }
    raw = json.dumps(header, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return _LENGTH.pack(len(raw)) + raw + b"".join(image.data for image in images)


def _encode_table(table: SheetTable) -> dict[str, Any]:
    return {
        "columns": list(table.columns),
        "rows": [[row.number, list(row.cells)] for row in table.rows],
        "other_sheets": list(table.other_sheets),
        "hidden_rows": table.hidden_rows,
        "hidden_columns": table.hidden_columns,
        "formulas_without_value": table.formulas_without_value,
    }


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


def _int_in(value: object, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise WireError("bad number")
    return value


def _decode_row(value: object, width: int) -> TableRow:
    if not isinstance(value, list) or len(value) != 2:
        raise WireError("bad row")
    number = _int_in(value[0], 1, MAX_ROWS_READ)
    cells = value[1]
    _expect(isinstance(cells, list) and len(cells) == width)
    for cell in cells:
        _expect(
            cell is None
            or (isinstance(cell, str) and len(cell) <= MAX_CELL_CHARS and clean_cell(cell) == cell)
        )
    _expect(any(cell is not None for cell in cells))
    return TableRow(number=number, cells=tuple(cells))


def _decode_table(value: object, kind: str, warnings: list[str]) -> SheetTable | None:
    if kind not in SPREADSHEET_KINDS:
        _expect(value is None)
        return None
    if not isinstance(value, dict) or set(value) != _TABLE_KEYS:
        raise WireError("bad table")
    columns, raw_rows, names = value["columns"], value["rows"], value["other_sheets"]
    _expect(isinstance(columns, list) and 1 <= len(columns) <= MAX_TABLE_COLUMNS)
    numbers = [_int_in(column, 1, MAX_SHEET_COLUMN) for column in columns]
    _expect(all(a < b for a, b in itertools.pairwise(numbers)))
    _expect(isinstance(raw_rows, list) and 1 <= len(raw_rows) <= MAX_TABLE_ROWS)
    rows = [_decode_row(row, len(numbers)) for row in raw_rows]
    _expect(all(a.number < b.number for a, b in itertools.pairwise(rows)))
    chars = sum(len(cell) for row in rows for cell in row.cells if cell is not None)
    _expect(chars <= MAX_TABLE_CHARS)
    _expect(all(any(row.cells[i] is not None for row in rows) for i in range(len(numbers))))
    _expect(isinstance(names, list) and len(names) <= MAX_SHEET_NAMES)
    for name in names:
        _expect(isinstance(name, str) and clean_sheet_name(name) == name)
    hidden_rows = _int_in(value["hidden_rows"], 0, MAX_ROWS_READ * MAX_SHEET_COLUMN)
    hidden_columns = _int_in(value["hidden_columns"], 0, MAX_SHEET_COLUMN)
    formulas = _int_in(value["formulas_without_value"], 0, MAX_ROWS_READ * MAX_SHEET_COLUMN)
    # Details and warnings agree (the warning may come without a usable sheet name).
    _expect(not names or "other_sheets_ignored" in warnings)
    _expect(bool(hidden_rows or hidden_columns) == ("hidden_cells_ignored" in warnings))
    _expect(bool(formulas) == ("formula_without_value" in warnings))
    return SheetTable(
        columns=tuple(numbers),
        rows=tuple(rows),
        other_sheets=tuple(names),
        hidden_rows=hidden_rows,
        hidden_columns=hidden_columns,
        formulas_without_value=formulas,
    )


def _decode_document(header: dict[str, Any], body: bytes) -> ParsedDocument:
    keys = {"v", "status", "kind", "text", "page_count", "warnings", "table"}
    _expect(set(header) == keys | {"claimed_from", "claimed_date", "images"})
    kind, page_count, warnings = header["kind"], header["page_count"], header["warnings"]
    _expect(_member(kind, _KINDS))
    _expect(type(page_count) is int and 1 <= page_count <= MAX_PDF_PAGES)
    _expect(isinstance(warnings, list) and all(_member(w, _KIND_WARNINGS[kind]) for w in warnings))
    _expect(len(warnings) == len(set(warnings)))
    text = _decode_text(header["text"])
    claimed_from = _decode_claimed_from(header["claimed_from"])
    claimed_date = _decode_claimed_date(header["claimed_date"])
    images = _decode_images(header["images"], body)
    table = _decode_table(header["table"], kind, warnings)
    if table is not None:
        _expect(text is None and not images and page_count == 1)
        _expect(claimed_from is None and claimed_date is None)
    else:
        _expect(text is not None or bool(images))
    return ParsedDocument(
        kind=kind,
        text=text,
        images=images,
        page_count=page_count,
        warnings=tuple(warnings),
        claimed_from=claimed_from,
        claimed_date=claimed_date,
        table=table,
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
    _expect(isinstance(header, dict) and type(header.get("v")) is int and header["v"] == VERSION)
    status = header.get("status")
    if status == "rejected":
        _expect(not body)
        return _decode_rejection(header)
    _expect(status == "ok")
    return _decode_document(header, body)

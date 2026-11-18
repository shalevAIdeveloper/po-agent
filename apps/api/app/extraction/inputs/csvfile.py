"""CSV: encoding, delimiter sniffing (detection) and the table (parser).

Encoding (Q-PR5-4, the plan's recommendation): UTF-8 (a BOM is stripped), UTF-16 with a
BOM, otherwise Windows-1252 with the `encoding_guessed` warning. A NUL or another control
character (other than tab, line breaks and form feed) means it isn't text.

Delimiter: our own linear scan over the first records (`csv.Sniffer` is regex-based and
slow on hostile input): `,` `;` tab `|`, counted outside double quotes. A delimiter must
appear in at least two records and in at least half of them.
"""

import codecs
import csv
import io
from dataclasses import dataclass
from typing import Final

from app.extraction.inputs.errors import InputError, InputWarningCode
from app.extraction.inputs.limits import CSV_SNIFF_BYTES, MAX_CELL_CHARS, MAX_ROWS_READ
from app.extraction.inputs.table import SpreadsheetParse, TableBuilder

DELIMITERS: Final = (",", ";", "\t", "|")
SNIFF_RECORDS: Final = 50
_UTF16_BOMS: Final = (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)
_ALLOWED_CONTROLS: Final = frozenset("\t\n\r\x0c")
# Markup or JSON with commas in it is not a CSV.
_NOT_CSV_START: Final = frozenset("<{")


@dataclass(frozen=True, slots=True)
class Decoded:
    text: str
    guessed: bool  # Windows-1252 fallback: `encoding_guessed`


def _decode(data: bytes, *, final: bool) -> Decoded:
    """`final=False` decodes a prefix (detection): a cut multi-byte character is fine."""
    if data.startswith(codecs.BOM_UTF8):
        text = codecs.getincrementaldecoder("utf-8")().decode(data[3:], final=final)
        return Decoded(text=text, guessed=False)
    if data.startswith(_UTF16_BOMS):
        # The utf-16 codec reads the BOM to pick the byte order and drops it.
        text = codecs.getincrementaldecoder("utf-16")().decode(data, final=final)
        return Decoded(text=text, guessed=False)
    try:
        text = codecs.getincrementaldecoder("utf-8")().decode(data, final=final)
        return Decoded(text=text, guessed=False)
    except UnicodeDecodeError:
        return Decoded(text=data.decode("cp1252", errors="replace"), guessed=True)


def decode(data: bytes) -> Decoded:
    """The whole file. A BOM that lies (bytes that don't decode) is `invalid_file`."""
    try:
        decoded = _decode(data, final=True)
    except UnicodeDecodeError:
        raise InputError("invalid_file") from None
    if not is_text(decoded.text):
        raise InputError("invalid_file")
    return decoded


def is_text(text: str) -> bool:
    return not any((ch < " " and ch not in _ALLOWED_CONTROLS) or ch == "\x7f" for ch in text)


def sniff_delimiter(text: str) -> str | None:
    """The delimiter of the first SNIFF_RECORDS non-blank records, or None. Linear time."""
    records: list[dict[str, int]] = []
    current = dict.fromkeys(DELIMITERS, 0)
    has_text = False
    in_quotes = False
    for ch in text:
        if ch == '"':
            in_quotes = not in_quotes
            has_text = True
        elif in_quotes:
            continue
        elif ch in "\r\n":
            if has_text:
                records.append(current)
                if len(records) >= SNIFF_RECORDS:
                    break
            current = dict.fromkeys(DELIMITERS, 0)
            has_text = False
        elif ch in current:
            current[ch] += 1
            has_text = True
        elif not ch.isspace():
            has_text = True
    else:
        if has_text:
            records.append(current)
    best: str | None = None
    best_records = 0
    for delimiter in DELIMITERS:
        with_it = sum(1 for record in records if record[delimiter])
        if with_it > best_records:
            best, best_records = delimiter, with_it
    if best is None or best_records < 2 or 2 * best_records < len(records):
        return None
    return best


def parse_csv(data: bytes) -> SpreadsheetParse:
    """Every record read, streamed; a cell starting with `=` is text, never evaluated."""
    decoded = decode(data)
    delimiter = sniff_delimiter(decoded.text[:CSV_SNIFF_BYTES])
    if delimiter is None:
        raise InputError("invalid_file")
    builder = TableBuilder()
    reader = csv.reader(io.StringIO(decoded.text, newline=""), delimiter=delimiter)
    # The limit is process-wide: restored afterwards (the worker parses in a child).
    previous = csv.field_size_limit(MAX_CELL_CHARS)
    try:
        for number, record in enumerate(reader, start=1):
            if number > MAX_ROWS_READ:
                raise InputError("too_many_rows")
            builder.add_row(number, enumerate(record, start=1))
    except csv.Error as error:
        # Only the start of the message is looked at; it is never logged or kept.
        if str(error).startswith("field larger than field limit"):
            raise InputError("document_too_large") from None
        raise InputError("invalid_file") from None
    finally:
        csv.field_size_limit(previous)
    warnings: tuple[InputWarningCode, ...] = ("encoding_guessed",) if decoded.guessed else ()
    return SpreadsheetParse(table=builder.build(), warnings=warnings)


def looks_like_csv(data: bytes) -> bool:
    try:
        head = _decode(data[:CSV_SNIFF_BYTES], final=False).text
    except UnicodeDecodeError:
        return False
    stripped = head.lstrip()
    if not stripped or stripped[0] in _NOT_CSV_START or not is_text(head):
        return False
    return sniff_delimiter(head) is not None

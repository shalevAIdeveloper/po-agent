"""Cell text to schema values, by code (Q-PR5-2, Q5): only unambiguous US forms.

A spreadsheet's values are never produced by the model: `extract_spreadsheet` copies a
cell's text or normalizes it here. Anything not clearly one value is `None`; the caller
adds an `unparsed_value` warning and `raw_text` keeps the cell as written.

- Numbers: digits with an optional fraction, thousands separators only in groups of
  three (`1,234.50`), an optional `$`, and a sign written as `-5.00`, `(5.00)` or
  `5.00-`. Refused: units (`12 CS`), European separators (`1.234,50`), `N/C`,
  exponents, other currencies or codes, more than 40 digits. The printed decimal places
  are kept (`1234.50`), `.75` is `0.75`, `12.` is `12`; `-0` is `0`.
- Line numbers: a whole number of at most 9 digits; `1a`, `10.1`, `#1` are refused.
- Dates: ISO `YYYY-MM-DD` (what the XLSX parser writes for a date cell, with or without
  a time) or US `M/D/YY` / `M/D/YYYY` (Q5: `03/04/26` is 2026-03-04; a 2-digit year
  is 20YY). Not a calendar date: refused.
"""

import re
from datetime import date
from decimal import Decimal
from typing import Final

MAX_DIGITS: Final = 40  # the same bound as grounding: no quantity or price is longer
_NUMBER_RE: Final = re.compile(r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]*)?|\.[0-9]+")
_LINE_NO_RE: Final = re.compile(r"[0-9]{1,9}")
_ISO_DATE_RE: Final = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})(?:T[0-9]{2}:[0-9]{2}(?::[0-9]{2})?)?"
)
_US_DATE_RE: Final = re.compile(r"([0-9]{1,2})/([0-9]{1,2})/([0-9]{4}|[0-9]{2})")


def to_decimal(text: str) -> Decimal | None:
    body = text.strip()
    negative = False
    if body.startswith("(") and body.endswith(")"):
        body, negative = body[1:-1].strip(), True
    elif body.endswith("-"):
        body, negative = body[:-1].strip(), True
    dollar = False
    for _ in range(2):  # "-$5", "$-5": a sign and a `$` in either order, once each
        if body.startswith("-") and not negative:
            body, negative = body[1:].strip(), True
        elif body.startswith("$") and not dollar:
            body, dollar = body[1:].strip(), True
    if not _NUMBER_RE.fullmatch(body):
        return None
    digits = body.replace(",", "")
    if digits.endswith("."):
        digits = digits[:-1]
    if digits.startswith("."):
        digits = "0" + digits
    if sum(ch.isdigit() for ch in digits) > MAX_DIGITS:
        return None
    value = Decimal(digits)
    if value == 0:
        return value  # never "-0"
    return -value if negative else value


def to_line_no(text: str) -> int | None:
    body = text.strip()
    return int(body) if _LINE_NO_RE.fullmatch(body) else None


def to_iso_date(text: str) -> date | None:
    body = text.strip()
    if match := _ISO_DATE_RE.fullmatch(body):
        year, month, day = (int(part) for part in match.groups())
    elif match := _US_DATE_RE.fullmatch(body):
        month, day, year = (int(part) for part in match.groups())
        if len(match.group(3)) == 2:
            year += 2000
    else:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None

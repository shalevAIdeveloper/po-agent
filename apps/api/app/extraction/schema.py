"""Extraction schema v1: what the model must return for one document (SPEC F2, §9).

The schema stores only what it is given; it never interprets, converts or computes:

- Every header field is nullable, and every key is required, so "absent" is an explicit
  `null` and never a dropped key. Nothing is invented (product principle 2).
- Dates: `po_date` / `requested_ship_date` are ISO `YYYY-MM-DD` or null, and the matching
  `*_raw` field keeps the date exactly as written. Reading `03/04/26` as US MM/DD (owner
  decision Q5) is the prompt's job (PR 3), not this module's; a parsed date without its
  raw text is rejected so every date stays traceable.
- Money and quantities are `Decimal`, exchanged as normalized strings (`"1234.50"`,
  `"-5"`) in both directions, so no value passes through a binary float. Printed forms
  (`"1,234.50"`, `"$12"`, `"12 CS"`, `"N/C"`) and JSON numbers are rejected, never
  reinterpreted: the model normalizes, `raw_text` keeps the printed form.
- `extended_price` / `order_total` are only ever the printed values; F4 computes and
  compares later. `uom` is kept as written (conversion is M2/M4).
- `confidence` / `header_confidence` are the model's uncalibrated self-report (Q6).

A breaking change is a new module and a new `schema_version` (`po_extraction.v2`).
The JSON Schema is committed next to this file; regenerate it with (from apps/api):

    uv run python -m app.extraction.schema app/extraction/po_extraction.v1.schema.json
"""

import json
import re
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Final, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    PlainValidator,
    WithJsonSchema,
    model_validator,
)

SCHEMA_VERSION: Final = "po_extraction.v1"

# ASCII digits only: Python's `\d` would also accept other scripts' digits.
_DECIMAL_PATTERN: Final = r"^-?[0-9]+(\.[0-9]+)?$"
_DECIMAL_RE: Final = re.compile(_DECIMAL_PATTERN)
_DATE_RE: Final = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

# Error messages never echo the input: PR 2 sends them back to the model and may log them.
_DECIMAL_ERROR: Final = (
    "must be a normalized decimal string: digits with an optional leading '-' and an "
    "optional '.' fraction; no thousands separators, currency symbols, units or exponents"
)


def _parse_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):  # Python callers (tests, fakes); JSON never yields one
        if not value.is_finite():
            raise ValueError("must be a finite decimal")
        text = format(value, "f")
    elif isinstance(value, str):
        text = value
    else:
        raise ValueError(_DECIMAL_ERROR)
    if not _DECIMAL_RE.fullmatch(text):
        raise ValueError(_DECIMAL_ERROR)
    return Decimal(text)


def _parse_date(value: object) -> date:
    if type(value) is date:  # not a datetime
        return value
    if isinstance(value, str) and _DATE_RE.fullmatch(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError("must be an ISO calendar date YYYY-MM-DD")


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must contain non-whitespace text")
    return value  # kept exactly as printed, not stripped


DecimalString = Annotated[
    Decimal,
    PlainValidator(_parse_decimal, json_schema_input_type=str),
    PlainSerializer(lambda d: format(d, "f"), return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "pattern": _DECIMAL_PATTERN}),
]
IsoDate = Annotated[
    date,
    PlainValidator(_parse_date, json_schema_input_type=str),
    PlainSerializer(lambda d: d.isoformat(), return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "format": "date"}),
]
# Every text field is non-blank or null: "" and "   " are rejected, so "not in the document"
# has exactly one spelling (null) and evals never have to treat "" as null.
Text = Annotated[str, Field(min_length=1), AfterValidator(_not_blank)]
RawText = Text
Confidence = Annotated[float, Field(ge=0, le=1, strict=True, allow_inf_nan=False)]
CurrencyCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# Field order is the order the model is asked to fill: raw text first, then its parts.


class Address(_Model):
    raw_text: RawText
    name: Text | None
    attention: Text | None
    line1: Text | None
    line2: Text | None
    city: Text | None
    state: Text | None
    postal_code: Text | None
    country: Text | None


class PurchaseOrderHeader(_Model):
    customer_name: Text | None
    customer_po_number: Text | None
    po_date_raw: Text | None
    po_date: IsoDate | None
    requested_ship_date_raw: Text | None
    requested_ship_date: IsoDate | None
    ship_to: Address | None
    bill_to: Address | None
    order_total: DecimalString | None
    currency: CurrencyCode | None
    payment_terms: Text | None
    notes: Text | None

    @model_validator(mode="after")
    def _dates_keep_raw_text(self) -> Self:
        for field in ("po_date", "requested_ship_date"):
            if getattr(self, field) is not None and getattr(self, f"{field}_raw") is None:
                raise ValueError(f"{field} is set but {field}_raw is null")
        return self


class PurchaseOrderLine(_Model):
    raw_text: RawText
    line_no: Annotated[int, Field(strict=True)] | None
    line_type: Literal["product", "charge", "comment"]
    customer_part_number: Text | None
    vendor_part_number: Text | None
    description: Text | None
    qty: DecimalString | None
    uom: Text | None
    unit_price: DecimalString | None
    extended_price: DecimalString | None
    page: Annotated[int, Field(ge=1, strict=True)] | None
    confidence: Confidence


class PurchaseOrderExtraction(_Model):
    schema_version: Literal["po_extraction.v1"]
    document_type: Literal["purchase_order", "not_a_po"]
    header: PurchaseOrderHeader
    header_confidence: Confidence
    lines: list[PurchaseOrderLine]

    @model_validator(mode="after")
    def _not_a_po_has_no_lines(self) -> Self:
        if self.document_type == "not_a_po" and self.lines:
            raise ValueError("document_type not_a_po must have no lines")
        return self


def json_schema() -> dict[str, Any]:
    return PurchaseOrderExtraction.model_json_schema(mode="validation")


def render() -> str:
    # Not key-sorted: property order is the fill order (see above) and pydantic emits it
    # deterministically, so the drift test stays stable.
    return json.dumps(json_schema(), indent=2) + "\n"


def main(argv: list[str]) -> None:
    if len(argv) != 2:
        raise SystemExit("usage: python -m app.extraction.schema <output.json>")
    Path(argv[1]).write_text(render(), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main(sys.argv)

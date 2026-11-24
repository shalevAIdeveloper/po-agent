import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.extraction.schema import (
    SCHEMA_VERSION,
    Address,
    PurchaseOrderExtraction,
    PurchaseOrderHeader,
    PurchaseOrderLine,
    json_schema,
    render,
)

COMMITTED = (
    Path(__file__).resolve().parents[2] / "app" / "extraction" / "po_extraction.v1.schema.json"
)

HEADER_FIELDS = {
    "customer_name",
    "customer_po_number",
    "po_date",
    "po_date_raw",
    "requested_ship_date",
    "requested_ship_date_raw",
    "ship_to",
    "bill_to",
    "notes",
    "order_total",
    "currency",
    "payment_terms",
}


def a_line(**overrides: Any) -> dict[str, Any]:
    line: dict[str, Any] = {
        "line_no": 1,
        "customer_part_number": "00123-A",
        "vendor_part_number": None,
        "description": "Widget, blue",
        "qty": "12",
        "uom": "CS",
        "unit_price": "1234.50",
        "price_per": None,
        "extended_price": None,
        "raw_text": "1  00123-A  Widget, blue  12 CS  $1,234.50",
        "line_type": "product",
        "confidence": 0.9,
        "page": 1,
    }
    line.update(overrides)
    return line


def null_header() -> dict[str, Any]:
    return dict.fromkeys(HEADER_FIELDS)


def a_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": "po_extraction.v1",
        "document_type": "purchase_order",
        "header": null_header(),
        "header_confidence": 0.5,
        "lines": [a_line()],
    }
    document.update(overrides)
    return document


def error_locs(exc: pytest.ExceptionInfo[ValidationError]) -> list[tuple[str | int, ...]]:
    return [tuple(error["loc"]) for error in exc.value.errors()]


# --- header ---------------------------------------------------------------------------


def test_all_null_header_is_valid() -> None:
    header = PurchaseOrderHeader.model_validate(null_header())

    assert all(value is None for value in header.model_dump().values())


HEADER_TEXT_FIELDS = [
    "customer_name",
    "customer_po_number",
    "po_date_raw",
    "requested_ship_date_raw",
    "payment_terms",
    "notes",
]
LINE_TEXT_FIELDS = ["customer_part_number", "vendor_part_number", "description", "uom"]
ADDRESS_TEXT_FIELDS = [
    "name",
    "attention",
    "line1",
    "line2",
    "city",
    "state",
    "postal_code",
    "country",
]
BLANKS = ["", " ", "\t\n"]


def an_address(**overrides: Any) -> dict[str, Any]:
    address: dict[str, Any] = dict.fromkeys(ADDRESS_TEXT_FIELDS)
    address["raw_text"] = "Acme Test Co, 1 Example St"
    address.update(overrides)
    return address


# Owner decision (2026-11-14): blank strings are rejected in v1, so "not in the document"
# has exactly one spelling, null.
@pytest.mark.parametrize("blank", BLANKS)
@pytest.mark.parametrize("field", HEADER_TEXT_FIELDS)
def test_blank_header_text_is_rejected(field: str, blank: str) -> None:
    with pytest.raises(ValidationError) as exc:
        PurchaseOrderHeader.model_validate({**null_header(), field: blank})

    assert error_locs(exc) == [(field,)]


@pytest.mark.parametrize("blank", BLANKS)
@pytest.mark.parametrize("field", LINE_TEXT_FIELDS)
def test_blank_line_text_is_rejected(field: str, blank: str) -> None:
    with pytest.raises(ValidationError) as exc:
        PurchaseOrderLine.model_validate(a_line(**{field: blank}))

    assert error_locs(exc) == [(field,)]


@pytest.mark.parametrize("blank", BLANKS)
@pytest.mark.parametrize("field", ADDRESS_TEXT_FIELDS)
def test_blank_address_text_is_rejected(field: str, blank: str) -> None:
    with pytest.raises(ValidationError) as exc:
        Address.model_validate(an_address(**{field: blank}))

    assert error_locs(exc) == [(field,)]


def test_text_fields_keep_surrounding_whitespace_as_printed() -> None:
    line = PurchaseOrderLine.model_validate(a_line(description=" Widget "))

    assert line.description == " Widget "


def test_json_schema_requires_non_empty_text_everywhere() -> None:
    defs = json_schema()["$defs"]
    expected = {
        "PurchaseOrderHeader": HEADER_TEXT_FIELDS,
        "PurchaseOrderLine": [*LINE_TEXT_FIELDS, "raw_text"],
        "Address": [*ADDRESS_TEXT_FIELDS, "raw_text"],
    }
    for model, fields in expected.items():
        for field in fields:
            prop = defs[model]["properties"][field]
            variants = prop.get("anyOf", [prop])
            strings = [v for v in variants if v.get("type") == "string"]
            assert strings, (model, field)
            assert all(v.get("minLength") == 1 for v in strings), (model, field)


def test_header_fields_are_exactly_the_approved_set() -> None:
    # SPEC F2 + owner decision Q4 (no buyer contact fields) + Q5 (raw dates).
    assert set(PurchaseOrderHeader.model_fields) == HEADER_FIELDS


def test_every_header_field_is_required_but_nullable() -> None:
    # Required: the model must state null explicitly instead of silently dropping a key.
    with pytest.raises(ValidationError) as exc:
        PurchaseOrderHeader.model_validate({})

    assert {loc[0] for loc in error_locs(exc)} == HEADER_FIELDS


def test_header_accepts_iso_dates_with_raw_text() -> None:
    header = PurchaseOrderHeader.model_validate(
        null_header() | {"po_date": "2026-03-04", "po_date_raw": "03/04/26"}
    )

    assert header.po_date == date(2026, 3, 4)
    assert header.po_date_raw == "03/04/26"


@pytest.mark.parametrize("value", ["03/04/26", "2026-3-4", "2026-03-04T00:00:00", 1772582400])
def test_header_dates_must_be_iso(value: object) -> None:
    with pytest.raises(ValidationError) as exc:
        PurchaseOrderHeader.model_validate(
            null_header() | {"po_date": value, "po_date_raw": "03/04/26"}
        )

    assert error_locs(exc) == [("po_date",)]


@pytest.mark.parametrize("field", ["po_date", "requested_ship_date"])
def test_a_parsed_date_needs_its_raw_text(field: str) -> None:
    with pytest.raises(ValidationError, match=f"{field}_raw"):
        PurchaseOrderHeader.model_validate(null_header() | {field: "2026-03-04"})


def test_raw_date_without_a_parsed_date_is_valid() -> None:
    header = PurchaseOrderHeader.model_validate(null_header() | {"requested_ship_date_raw": "ASAP"})

    assert header.requested_ship_date is None


@pytest.mark.parametrize("value", ["USD", "CAD"])
def test_currency_accepts_iso_4217_codes(value: str) -> None:
    assert PurchaseOrderHeader.model_validate(null_header() | {"currency": value}).currency == value


@pytest.mark.parametrize("value", ["$", "usd", "US$", "US Dollars", ""])
def test_currency_rejects_anything_but_an_iso_code(value: str) -> None:
    with pytest.raises(ValidationError) as exc:
        PurchaseOrderHeader.model_validate(null_header() | {"currency": value})

    assert error_locs(exc) == [("currency",)]


def test_address_requires_raw_text_and_allows_null_parts() -> None:
    address = Address.model_validate(
        {
            "name": None,
            "attention": None,
            "line1": None,
            "line2": None,
            "city": None,
            "state": None,
            "postal_code": None,
            "country": None,
            "raw_text": "ACME Corp\n12 Main St\nSpringfield, IL 62701",
        }
    )

    assert address.raw_text.startswith("ACME Corp")
    with pytest.raises(ValidationError):
        Address.model_validate(address.model_dump() | {"raw_text": "  "})
    with pytest.raises(ValidationError):
        Address.model_validate({k: v for k, v in address.model_dump().items() if k != "raw_text"})


# --- lines ----------------------------------------------------------------------------


def test_line_without_raw_text_is_rejected() -> None:
    line = a_line()
    del line["raw_text"]

    with pytest.raises(ValidationError) as exc:
        PurchaseOrderLine.model_validate(line)

    assert error_locs(exc) == [("raw_text",)]


@pytest.mark.parametrize("raw_text", ["", "   ", "\t\n", None])
def test_line_with_blank_raw_text_is_rejected(raw_text: str | None) -> None:
    with pytest.raises(ValidationError) as exc:
        PurchaseOrderLine.model_validate(a_line(raw_text=raw_text))

    assert error_locs(exc) == [("raw_text",)]


def test_raw_text_is_kept_exactly_as_printed() -> None:
    raw = "  10  ABC-01   2 EA  \n"

    assert PurchaseOrderLine.model_validate(a_line(raw_text=raw)).raw_text == raw


@pytest.mark.parametrize("confidence", [-0.01, 1.01, float("nan"), "0.9", True])
def test_line_confidence_out_of_range_or_not_a_number_is_rejected(confidence: object) -> None:
    with pytest.raises(ValidationError) as exc:
        PurchaseOrderLine.model_validate(a_line(confidence=confidence))

    assert error_locs(exc) == [("confidence",)]


@pytest.mark.parametrize("confidence", [0, 0.0, 1, 1.0])
def test_confidence_bounds_are_inclusive(confidence: float) -> None:
    assert PurchaseOrderLine.model_validate(a_line(confidence=confidence)).confidence == confidence


@pytest.mark.parametrize("confidence", [-0.5, 2.0])
def test_header_confidence_out_of_range_is_rejected(confidence: float) -> None:
    with pytest.raises(ValidationError) as exc:
        PurchaseOrderExtraction.model_validate(a_document(header_confidence=confidence))

    assert error_locs(exc) == [("header_confidence",)]


@pytest.mark.parametrize("page", [0, -1, "1"])
def test_page_must_be_a_positive_integer(page: object) -> None:
    with pytest.raises(ValidationError):
        PurchaseOrderLine.model_validate(a_line(page=page))


def test_page_may_be_null() -> None:
    assert PurchaseOrderLine.model_validate(a_line(page=None)).page is None


def test_line_type_is_a_closed_set() -> None:
    for line_type in ("product", "charge", "comment"):
        assert PurchaseOrderLine.model_validate(a_line(line_type=line_type)).line_type == line_type
    with pytest.raises(ValidationError):
        PurchaseOrderLine.model_validate(a_line(line_type="discount"))


def test_comment_line_may_have_only_raw_text() -> None:
    line = PurchaseOrderLine.model_validate(
        {
            "line_no": None,
            "customer_part_number": None,
            "vendor_part_number": None,
            "description": None,
            "qty": None,
            "uom": None,
            "unit_price": None,
            "price_per": None,
            "extended_price": None,
            "raw_text": "** Deliver to dock 4 **",
            "line_type": "comment",
            "confidence": 0.4,
            "page": None,
        }
    )

    assert line.qty is None


def test_extended_price_is_never_computed() -> None:
    line = PurchaseOrderLine.model_validate(a_line(qty="2", unit_price="3.00"))

    assert line.extended_price is None


def test_uom_is_kept_as_written() -> None:
    assert PurchaseOrderLine.model_validate(a_line(uom="case of 12")).uom == "case of 12"


# --- decimals -------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["qty", "unit_price", "extended_price"])
def test_line_decimal_round_trips_as_a_string(field: str) -> None:
    line = PurchaseOrderLine.model_validate(a_line(**{field: "1234.50"}))

    assert getattr(line, field) == Decimal("1234.50")
    dumped = line.model_dump(mode="json")
    assert dumped[field] == "1234.50"  # trailing zero kept, no float
    assert PurchaseOrderLine.model_validate(dumped) == line
    assert PurchaseOrderLine.model_validate_json(line.model_dump_json()) == line


def test_order_total_round_trips_as_a_string() -> None:
    header = PurchaseOrderHeader.model_validate(null_header() | {"order_total": "0.10"})

    assert header.model_dump(mode="json")["order_total"] == "0.10"
    assert json.loads(header.model_dump_json())["order_total"] == "0.10"


@pytest.mark.parametrize("value", ["0", "12", "0.5", "-5.00", "1234567890.123456"])
def test_normalized_decimal_strings_are_accepted(value: str) -> None:
    assert PurchaseOrderLine.model_validate(a_line(qty=value)).qty == Decimal(value)


@pytest.mark.parametrize(
    "value",
    [
        # Decision (pinned): printed formats are rejected, never reinterpreted. The model
        # must output a normalized number; raw_text keeps the printed form.
        "1,234.50",
        "1.234,50",
        "$12.00",
        "12 CS",
        "N/C",
        "1e3",
        "+5",
        ".5",
        "5.",
        " 5",
        "",
        "NaN",
        "Infinity",
    ],
)
def test_printed_or_non_normalized_numbers_are_rejected(value: str) -> None:
    with pytest.raises(ValidationError) as exc:
        PurchaseOrderLine.model_validate(a_line(unit_price=value))

    assert error_locs(exc) == [("unit_price",)]


@pytest.mark.parametrize("value", [12.5, 0.1, 12, True])
def test_json_numbers_are_rejected_for_decimals(value: object) -> None:
    # A JSON number goes through a binary float on most parsers (0.1 drifts); the
    # contract is a string, so the schema retry tells the model instead of guessing.
    with pytest.raises(ValidationError) as exc:
        PurchaseOrderLine.model_validate(a_line(qty=value))

    assert error_locs(exc) == [("qty",)]


def test_decimal_instances_are_accepted_from_python() -> None:
    line = PurchaseOrderLine.model_validate(a_line(qty=Decimal("1E+1")))

    assert line.model_dump(mode="json")["qty"] == "10"


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity")])
def test_non_finite_decimal_instances_are_rejected(value: Decimal) -> None:
    with pytest.raises(ValidationError):
        PurchaseOrderLine.model_validate(a_line(qty=value))


# --- price_per (M1-PRICE-NOTATION, owner 2026-11-24) ------------------------------------


@pytest.mark.parametrize("value", ["1", "100", "1000", "999999"])
def test_price_per_accepts_a_positive_whole_number_string(value: str) -> None:
    line = PurchaseOrderLine.model_validate(a_line(price_per=value))

    assert line.price_per == Decimal(value)
    dumped = line.model_dump(mode="json")
    assert dumped["price_per"] == value
    assert PurchaseOrderLine.model_validate(dumped) == line
    assert PurchaseOrderLine.model_validate_json(line.model_dump_json()) == line


def test_price_per_may_be_null() -> None:
    assert PurchaseOrderLine.model_validate(a_line(price_per=None)).price_per is None


@pytest.mark.parametrize(
    "value",
    ["0", "-100", "12.5", "100.0", "1,000", "0100", "+100", " 100", "", "/C", "C", "1000000"],
)
def test_price_per_rejects_anything_but_a_positive_whole_number(value: str) -> None:
    with pytest.raises(ValidationError) as exc:
        PurchaseOrderLine.model_validate(a_line(price_per=value))

    assert error_locs(exc) == [("price_per",)]


@pytest.mark.parametrize("value", [100, 100.0, True])
def test_price_per_rejects_json_numbers(value: object) -> None:
    with pytest.raises(ValidationError) as exc:
        PurchaseOrderLine.model_validate(a_line(price_per=value))

    assert error_locs(exc) == [("price_per",)]


@pytest.mark.parametrize("value", [Decimal("0"), Decimal("-1"), Decimal("1.5"), Decimal("NaN")])
def test_price_per_rejects_bad_decimal_instances(value: Decimal) -> None:
    with pytest.raises(ValidationError):
        PurchaseOrderLine.model_validate(a_line(price_per=value))


def test_price_per_accepts_a_whole_decimal_instance() -> None:
    line = PurchaseOrderLine.model_validate(a_line(price_per=Decimal("1E+2")))

    assert line.model_dump(mode="json")["price_per"] == "100"


def test_price_per_is_required() -> None:
    line = a_line()
    del line["price_per"]

    with pytest.raises(ValidationError) as exc:
        PurchaseOrderLine.model_validate(line)

    assert error_locs(exc) == [("price_per",)]


def test_price_per_follows_unit_price_in_the_json_schema() -> None:
    line = json_schema()["$defs"]["PurchaseOrderLine"]["properties"]
    names = list(line)

    assert names.index("price_per") == names.index("unit_price") + 1
    [string] = [v for v in line["price_per"]["anyOf"] if v.get("type") == "string"]
    assert string["pattern"] == "^[1-9][0-9]{0,5}$"


def test_price_per_does_not_change_unit_or_extended_price() -> None:
    # Never computed: the printed unit and extended prices stay as given.
    line = PurchaseOrderLine.model_validate(
        a_line(qty="5", unit_price="12.50", price_per="100", extended_price="62.50")
    )

    assert (line.unit_price, line.extended_price) == (Decimal("12.50"), Decimal("62.50"))


# --- document -------------------------------------------------------------------------


def test_a_full_document_round_trips() -> None:
    document = PurchaseOrderExtraction.model_validate(a_document())

    assert document.schema_version == SCHEMA_VERSION == "po_extraction.v1"
    assert PurchaseOrderExtraction.model_validate_json(document.model_dump_json()) == document


def test_wrong_schema_version_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        PurchaseOrderExtraction.model_validate(a_document(schema_version="po_extraction.v2"))

    assert error_locs(exc) == [("schema_version",)]


@pytest.mark.parametrize(
    ("model", "data"),
    [
        (PurchaseOrderExtraction, a_document(approved=True)),
        (PurchaseOrderHeader, null_header() | {"buyer_email": "a@example.com"}),
        (PurchaseOrderLine, a_line(discount="5")),
        (Address, {"raw_text": "x", "zip": "12345"}),
    ],
)
def test_extra_fields_are_rejected(model: type[Any], data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError) as exc:
        model.model_validate(data)

    assert "extra_forbidden" in {error["type"] for error in exc.value.errors()}


def test_not_a_po_with_lines_is_rejected() -> None:
    with pytest.raises(ValidationError, match="not_a_po"):
        PurchaseOrderExtraction.model_validate(a_document(document_type="not_a_po"))


def test_not_a_po_without_lines_is_valid() -> None:
    document = PurchaseOrderExtraction.model_validate(
        a_document(document_type="not_a_po", lines=[])
    )

    assert document.lines == []


def test_purchase_order_without_lines_is_valid() -> None:
    # An unreadable or empty PO is reviewed by a human; the schema does not reject it.
    assert PurchaseOrderExtraction.model_validate(a_document(lines=[])).lines == []


def test_validation_errors_do_not_echo_document_text() -> None:
    # PR 2 feeds errors back to the model and may log them: they must be safe to log
    # when rendered without input (include_input=False), as SPEC §10 requires.
    secret = "SECRET-CUSTOMER-TEXT 1,234.50"
    with pytest.raises(ValidationError) as exc:
        PurchaseOrderLine.model_validate(a_line(unit_price=secret, raw_text=" "))

    rendered = json.dumps(exc.value.errors(include_input=False, include_url=False), default=str)
    assert "SECRET" not in rendered


# --- JSON Schema ----------------------------------------------------------------------


def test_committed_schema_is_up_to_date() -> None:
    """Regenerate with (from apps/api):
    uv run python -m app.extraction.schema app/extraction/po_extraction.v1.schema.json
    """
    assert COMMITTED.read_text(encoding="utf-8") == render()


def test_serialization_schema_matches_validation_schema() -> None:
    # What we store (model_dump) is described by the same contract the model is given.
    assert PurchaseOrderExtraction.model_json_schema(mode="serialization") == json_schema()


def test_schema_forbids_additional_properties_everywhere() -> None:
    schema = json_schema()
    objects = [schema, *schema["$defs"].values()]

    for obj in objects:
        assert obj["type"] == "object"
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"]), obj.get("title")


def test_schema_uses_only_portable_constructs() -> None:
    # Structured-output modes accept a JSON Schema subset; keep to the common core
    # (PR 2 checks the provider's exact list).
    allowed = {
        "$defs",
        "$ref",
        "additionalProperties",
        "anyOf",
        "const",
        "enum",
        "format",
        "items",
        "maximum",
        "minimum",
        "minLength",
        "pattern",
        "properties",
        "required",
        "title",
        "type",
    }

    def walk(node: object) -> None:
        if isinstance(node, dict):
            unknown = set(node) - allowed
            assert not unknown, unknown
            for key, value in node.items():
                if key in ("properties", "$defs"):
                    for child in value.values():
                        walk(child)
                elif key != "required":
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(json_schema())

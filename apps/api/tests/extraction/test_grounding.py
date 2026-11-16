from typing import Any

import pytest

from app.extraction.grounding import check_grounding
from app.extraction.schema import PurchaseOrderExtraction
from tests.extraction.test_schema import a_document, a_line

SOURCE = """ACME Supply  PURCHASE ORDER  PO# 4500012345
Ln  Item        Description       Qty    UOM  Unit       Ext
1   00123-A     Widget, blue      12     CS   $1,234.50  $14,814.00
2   BX 77 / 9   Bolt kit          1.000  EA   .75        .75
Total: $14,814.75
"""


def extraction(lines: list[dict[str, Any]], **header: Any) -> PurchaseOrderExtraction:
    document = a_document(lines=lines)
    document["header"].update(header)
    return PurchaseOrderExtraction.model_validate(document)


def paths(result: PurchaseOrderExtraction, source: str = SOURCE) -> list[str]:
    warnings = check_grounding(result, source)
    assert all(warning.code == "ungrounded" for warning in warnings)
    return [warning.path for warning in warnings]


def test_values_printed_on_the_document_are_grounded() -> None:
    result = extraction(
        [
            a_line(qty="12", unit_price="1234.50", extended_price="14814.00"),
            a_line(
                line_no=2,
                customer_part_number="BX 77 / 9",
                vendor_part_number=None,
                qty="1",
                uom="EA",
                unit_price="0.75",
                extended_price="0.75",
            ),
        ],
        customer_po_number="4500012345",
        order_total="14814.75",
    )
    assert paths(result) == []


def test_part_numbers_match_ignoring_case_and_whitespace() -> None:
    # A PDF text layer can split or re-space tokens; the model copies the printed form.
    result = extraction([a_line(customer_part_number="bx77/9", qty=None, unit_price=None)])
    assert paths(result) == []


def test_invented_part_number_is_flagged() -> None:
    result = extraction([a_line(customer_part_number="00123-B")])
    assert paths(result) == ["lines.0.customer_part_number"]


def test_invented_vendor_part_number_is_flagged() -> None:
    result = extraction([a_line(vendor_part_number="V-555")])
    assert paths(result) == ["lines.0.vendor_part_number"]


def test_invented_po_number_is_flagged() -> None:
    result = extraction([a_line()], customer_po_number="4500099999")
    assert paths(result) == ["header.customer_po_number"]


@pytest.mark.parametrize(
    ("field", "value"),
    [("qty", "13"), ("unit_price", "1234.05"), ("extended_price", "14814.01")],
)
def test_invented_numbers_are_flagged(field: str, value: str) -> None:
    result = extraction([a_line(**{field: value})])
    assert paths(result) == [f"lines.0.{field}"]


def test_computed_order_total_is_flagged() -> None:
    result = extraction([a_line()], order_total="14815.00")
    assert paths(result) == ["header.order_total"]


def test_null_values_are_never_flagged() -> None:
    result = extraction(
        [
            a_line(
                customer_part_number=None,
                vendor_part_number=None,
                qty=None,
                unit_price=None,
                extended_price=None,
            )
        ]
    )
    assert paths(result, "nothing here") == []


def test_numbers_match_by_value_not_by_spelling() -> None:
    result = extraction([a_line(qty="12.00", unit_price="1234.5", extended_price="14814")])
    assert paths(result) == []


def test_a_number_inside_a_longer_number_does_not_count() -> None:
    # "123" appears only as part of "4500012345" / "00123-A"... and "00123" reads as 123,
    # which is accepted: grounding errs toward fewer warnings, it never changes a value.
    result = extraction([a_line(qty="4500")], customer_po_number="4500012345")
    assert paths(result) == ["lines.0.qty"]


def test_fullwidth_text_layer_grounds_ascii_values() -> None:
    source = "".join(chr(0xFF00 + ord(ch) - 0x20) for ch in "AB-12") + "  qty " + chr(0xFF11) * 2
    result = extraction([a_line(customer_part_number="AB-12", qty="11", unit_price=None)])
    assert paths(result, source) == []


def test_negative_values_ground_on_parentheses_or_minus() -> None:
    result = extraction(
        [a_line(customer_part_number=None, qty=None, unit_price="-5.00", extended_price="-5")]
    )
    assert paths(result, "Discount (5.00)  -5") == []


def test_a_huge_number_in_the_document_does_not_crash() -> None:
    # `sec` M1: abs() on a 1,000,001-digit Decimal raised decimal.Overflow.
    result = extraction([a_line()], customer_po_number=None)
    source = SOURCE + " total " + "9" * 1_000_001
    assert paths(result, source) == []


def test_a_huge_number_from_the_model_is_flagged_not_a_crash() -> None:
    result = extraction([a_line(qty="9" * 1_000_001)])
    assert paths(result) == ["lines.0.qty"]


def test_flags_follow_line_order() -> None:
    result = extraction([a_line(), a_line(customer_part_number="NOPE", qty="99")])
    assert paths(result) == ["lines.1.customer_part_number", "lines.1.qty"]

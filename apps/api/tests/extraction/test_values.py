from datetime import date

import pytest

from app.extraction.values import to_decimal, to_iso_date, to_line_no


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("12", "12"),
        (" 12 ", "12"),
        ("12\n", "12"),  # surrounding whitespace only
        ("1234.50", "1234.50"),
        ("1,234.50", "1234.50"),
        ("1,234,567", "1234567"),
        ("$12", "12"),
        ("$ 1,234.50", "1234.50"),
        (".75", "0.75"),
        ("12.", "12"),
        ("0.250", "0.250"),
        ("100000000000000000000", "100000000000000000000"),
        # Negatives: kept, with the sign (the caller flags them).
        ("-5.00", "-5.00"),
        ("(5.00)", "-5.00"),
        ("5.00-", "-5.00"),
        ("($5.00)", "-5.00"),
        ("-$5.00", "-5.00"),
        ("$-5.00", "-5.00"),
        ("$5.00-", "-5.00"),
        ("(1,234.50)", "-1234.50"),
        ("0.00", "0.00"),
        ("(0.00)", "0.00"),
        ("-0", "0"),
    ],
)
def test_unambiguous_us_numbers(text: str, value: str) -> None:
    result = to_decimal(text)
    assert result is not None
    assert format(result, "f") == value


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "$",
        "-",
        "()",
        "12 CS",
        "CS 12",
        "1.234,50",
        "1,234,50",
        "12,34",
        "1,2345.00",
        ",123",
        "N/C",
        "TBD",
        "1e5",
        "1E+20",
        "€12",  # euro
        "£12",  # pound
        "USD 12",
        "12 USD",
        "--5",
        "(-5)",
        "-5-",
        "(5)-",
        "$$5",
        "1.2.3",
        "+5",
        chr(0xFF11) + chr(0xFF12),  # fullwidth digits
        chr(0x0661) + chr(0x0662),  # Arabic-Indic digits
        "1" * 41,
        "Infinity",
        "NaN",
    ],
)
def test_anything_else_is_not_a_number(text: str) -> None:
    assert to_decimal(text) is None


def test_no_negative_zero() -> None:
    zero = to_decimal("(0.00)")
    assert zero is not None and not zero.is_signed()


@pytest.mark.parametrize(
    ("text", "value"),
    [("1", 1), (" 10 ", 10), ("007", 7), ("123456789", 123456789)],
)
def test_line_numbers(text: str, value: int) -> None:
    assert to_line_no(text) == value


@pytest.mark.parametrize("text", ["", "1a", "10.1", "#1", "-1", "1,000", "1234567890", "1.0"])
def test_not_line_numbers(text: str) -> None:
    assert to_line_no(text) is None


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("2026-11-16", date(2026, 11, 16)),
        ("2026-11-16T09:30:00", date(2026, 11, 16)),
        ("03/04/26", date(2026, 3, 4)),  # Q5: US month/day/year
        ("3/4/2026", date(2026, 3, 4)),
        ("12/31/99", date(2099, 12, 31)),
        (" 11/16/2026 ", date(2026, 11, 16)),
        ("02/29/2028", date(2028, 2, 29)),
    ],
)
def test_dates(text: str, value: date) -> None:
    assert to_iso_date(text) == value


@pytest.mark.parametrize(
    "text",
    [
        "",
        "13/01/26",
        "02/30/26",
        "02/29/2027",
        "2026-13-01",
        "16.11.2026",
        "11-16-2026",
        "Nov 16, 2026",
        "11/16",
        "1/2/123",
        "2026/11/16",
        "26-11-16",
    ],
)
def test_not_dates(text: str) -> None:
    assert to_iso_date(text) is None


@pytest.mark.parametrize(
    "text",
    ["0,123", "00,123", "000,000", "$0,123", "(0,123)", "0,123.50", "-0,123", "0,123-", "01,234"],
)
def test_a_thousands_group_after_a_leading_zero_is_not_a_number(text: str) -> None:
    # qa: a European reader means 0.123; the first group can't start with 0.
    assert to_decimal(text) is None


@pytest.mark.parametrize(
    ("text", "value"),
    [("0", "0"), ("0.50", "0.50"), ("$0.75", "0.75"), ("1,234", "1234"), ("007", "7")],
)
def test_a_leading_zero_without_groups_is_still_fine(text: str, value: str) -> None:
    result = to_decimal(text)
    assert result is not None and format(result, "f") == value

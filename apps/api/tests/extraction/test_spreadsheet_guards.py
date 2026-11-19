"""Guards the T11 hand-mutation pass found untested (M1 PR 5).

Each test here fails under a mutation of one guard that every earlier test survived:
- `check_mapping`: an address cell must be in the sample, not only in the table;
- the `negative_value` warning: zero (however printed) is not negative;
- the 0.5 confidence cap: a cap, never a floor (a lower mapping confidence is kept).
"""

import json
from decimal import Decimal

import pytest

from app.extraction.column_mapping import ColumnMapping, build_sample, check_mapping
from app.extraction.inputs.table import TableRow
from app.extraction.spreadsheet import extract_spreadsheet
from app.llm.fake import FakeLLMClient, FakeResponse
from tests.extraction.test_column_mapping import mapping, po_table
from tests.extraction.test_spreadsheet import SETTINGS, table


def test_an_address_cell_in_the_table_but_not_in_the_sample_is_refused() -> None:
    t = po_table(lines=100)  # rows 6..105: the sample skips the middle rows
    sample = build_sample(t)
    assert 60 not in sample.rows
    value = mapping(addresses=[{"field": "bill_to", "cells": [{"row": 60, "column": "A"}]}])
    issues = check_mapping(ColumnMapping.model_validate(value), t, sample)
    assert [(i.path, i.type) for i in issues] == [("addresses.0.cells.0", "cell_not_in_sample")]


@pytest.mark.parametrize("printed", ["0", "0.00", "(0.00)", "-0", "0.00-", "$0"])
async def test_zero_is_not_flagged_as_negative(printed: str) -> None:
    t = table(("FREE-1", "Sample", printed, printed, None), ("Total", None, None, "0", None))
    client = FakeLLMClient([FakeResponse(json.dumps(mapping(footer_rows=[7])))])
    result = await extract_spreadsheet(client, t, SETTINGS)
    assert result.extraction is not None
    (line,) = result.extraction.lines
    assert line.qty == Decimal(0) and line.unit_price == Decimal(0)
    assert not line.qty.is_signed()  # never "-0"
    assert result.warnings == ()


async def test_the_unparsed_cap_never_raises_a_lower_confidence() -> None:
    t = table(
        ("00123-A", "Widget", "12 CS", "1.50", None),
        ("00124", "Bolt", "3", "0.25", None),
        ("Total", None, None, "1", None),
    )
    client = FakeLLMClient([FakeResponse(json.dumps(mapping(confidence=0.3, footer_rows=[8])))])
    result = await extract_spreadsheet(client, t, SETTINGS)
    assert result.extraction is not None
    assert [line.confidence for line in result.extraction.lines] == [0.3, 0.3]
    assert result.extraction.header_confidence == 0.3


async def test_only_the_line_with_an_unparsed_value_is_capped() -> None:
    t = table(
        ("00123-A", "Widget", "12", "1.50", None),
        ("00124", "Bolt", "3", "N/C", None),
        ("00125", "Nut", "4", "0.10", None),
        ("Total", None, None, "1", None),
        header=(
            TableRow(1, ("PO#: 4500012345", None, None, None, None)),
            TableRow(2, ("Ship to", "Acme Corp", None, None, None)),
            TableRow(3, (None, "1 Main St, Springfield", None, None, None)),
            TableRow(5, ("Item", "Description", "Qty", "Price", "Note")),
        ),
    )
    client = FakeLLMClient([FakeResponse(json.dumps(mapping(footer_rows=[9])))])
    result = await extract_spreadsheet(client, t, SETTINGS)
    assert result.extraction is not None
    assert [line.confidence for line in result.extraction.lines] == [0.9, 0.5, 0.9]

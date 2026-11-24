"""The correct column mapping of a sheet case, as a `column_mapping.v1` object.

`tests/evals/test_oracle.py` runs PR 5's own code with it (`check_mapping`,
`build_extraction`, `table_warnings`, grounding): the result must equal `expected.json`
on every scored field, and the warnings must equal `meta.expected_extraction_warnings`.
That proves each sheet case's truth is reachable by a perfect mapping model, except for
the rows a `known_gap` names.
"""

from app.extraction.column_mapping import SCHEMA_VERSION, ColumnMapping
from evals.generate.spec import Oracle

PERFECT_CONFIDENCE = 1.0


def column_mapping(oracle: Oracle) -> ColumnMapping:
    return ColumnMapping.model_validate(
        {
            "schema_version": SCHEMA_VERSION,
            "document_type": oracle.document_type,
            "first_data_row": oracle.first_data_row,
            "columns": [{"column": c, "field": f} for c, f in oracle.columns],
            "header_values": [
                {"field": r.field, "row": r.row, "column": r.column, "value": r.value}
                for r in oracle.header_values
            ],
            "addresses": [
                {"field": name, "cells": [{"row": row, "column": col} for row, col in cells]}
                for name, cells in oracle.addresses
            ],
            "footer_rows": list(oracle.footer_rows),
            "confidence": PERFECT_CONFIDENCE,
        }
    )

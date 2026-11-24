"""Sheet cases against PR 5's own code (M1 PR 6 T8): a perfect mapping reaches the truth.

With the oracle mapping, `check_mapping` accepts it, and `build_extraction` (+ the table
warnings and the grounding self-check) gives `expected.json` on every scored field and
exactly `meta.expected_extraction_warnings`. A `known_gap` case differs only by the
lines PR 5 builds from its gap rows, and the test proves that difference is there.
"""

from typing import Any

import pytest

from app.extraction.column_mapping import build_sample, check_mapping
from app.extraction.grounding import ExtractionWarning, check_grounding
from app.extraction.inputs.table import SheetTable
from app.extraction.schema import PurchaseOrderExtraction
from app.extraction.spreadsheet import (
    CELL_SEPARATOR,
    build_extraction,
    source_text,
    table_warnings,
)
from evals.generate import expected, meta, oracle
from evals.generate.cases import CASES
from evals.generate.files import EXPECTED_NAME, META_NAME
from evals.generate.spec import Case, SheetDoc
from evals.run import FIXTURES_DIR
from tests.evals.support import parsed

SHEETS = [pytest.param(c, id=c.name) for c in CASES if isinstance(c.document, SheetDoc)]


def _doc(case: Case) -> SheetDoc:
    assert isinstance(case.document, SheetDoc)
    return case.document


def _table(case: Case) -> SheetTable:
    table = parsed(case.name).table
    assert table is not None
    return table


def _run(case: Case) -> tuple[PurchaseOrderExtraction, tuple[ExtractionWarning, ...]]:
    table = _table(case)
    mapping = oracle.column_mapping(_doc(case).oracle)
    extraction, line_warnings = build_extraction(mapping, table)
    warnings = table_warnings(table, parsed(case.name).warnings) + line_warnings
    return extraction, warnings + check_grounding(extraction, source_text(table))


def _gap_texts(case: Case) -> set[str]:
    rows = {row.number: row for row in _table(case).rows}
    return {
        CELL_SEPARATOR.join(c for c in rows[n].cells if c is not None) for n in _doc(case).gap_rows
    }


def _scored(extraction: PurchaseOrderExtraction, not_scored: list[str]) -> dict[str, Any]:
    """Python values (Decimal compares by value: "18.4" == "18.40"), confidences and
    not-scored paths set to None."""
    data = extraction.model_dump()
    data["header_confidence"] = None
    for line in data["lines"]:
        line["confidence"] = None
    for path in not_scored:
        parts = path.split(".")
        targets = data["lines"] if parts[0] == "lines" and parts[1] == "*" else None
        if targets is not None:
            for line in targets:
                line[parts[2]] = None
            continue
        node: Any = data
        for part in parts[:-1]:
            node = node[int(part)] if part.isdigit() else node[part]
            if node is None:
                break
        if node is not None:
            node[parts[-1]] = None
    return data


def _meta(case: Case) -> meta.CaseMeta:
    return meta.load((FIXTURES_DIR / case.name / META_NAME).read_bytes())


def _expected(case: Case) -> PurchaseOrderExtraction:
    return expected.load((FIXTURES_DIR / case.name / EXPECTED_NAME).read_bytes())


def test_there_are_eleven_sheet_cases_four_follow_ups_and_two_known_gaps() -> None:
    assert len(SHEETS) == 11
    gaps = sorted(c.name for c in CASES if c.known_gap)
    assert gaps == ["xlsx_mid_table_subtotal", "xlsx_repeated_header_mid_table"]
    assert all(c.known_gap == ("M1-SHEET-TAIL",) for c in CASES if c.known_gap)


@pytest.mark.parametrize("case", SHEETS)
def test_the_oracle_mapping_passes_pr5s_checks(case: Case) -> None:
    table = _table(case)
    mapping = oracle.column_mapping(_doc(case).oracle)
    assert check_mapping(mapping, table, build_sample(table)) == ()


@pytest.mark.parametrize("case", SHEETS)
def test_the_warnings_are_the_meta_warnings(case: Case) -> None:
    _, warnings = _run(case)
    found = [
        (
            w.code,
            w.path,
            None
            if w.details is None
            else (
                list(w.details.names),
                list(w.details.rows),
                list(w.details.texts),
                w.details.hidden_rows,
                w.details.hidden_columns,
                w.details.count,
            ),
        )
        for w in warnings
    ]
    wanted = [
        (
            w.code,
            w.path,
            None
            if w.details is None
            else (
                w.details.names,
                w.details.rows,
                w.details.texts,
                w.details.hidden_rows,
                w.details.hidden_columns,
                w.details.count,
            ),
        )
        for w in _meta(case).expected_extraction_warnings
    ]
    assert found == wanted


@pytest.mark.parametrize("case", SHEETS)
def test_the_oracle_extraction_is_the_truth_on_every_scored_field(case: Case) -> None:
    extraction, _ = _run(case)
    truth = _expected(case)
    not_scored = _meta(case).not_scored
    gaps = _gap_texts(case)
    kept = [line for line in extraction.lines if line.raw_text not in gaps]

    assert len(extraction.lines) - len(kept) == len(_doc(case).gap_rows)
    patched = extraction.model_copy(update={"lines": kept})
    assert _scored(patched, not_scored) == _scored(truth, not_scored)


@pytest.mark.parametrize("case", [p for p in SHEETS if p.values[0].known_gap])  # type: ignore[union-attr]
def test_a_known_gap_really_changes_the_result(case: Case) -> None:
    extraction, _ = _run(case)
    truth = _expected(case)
    assert len(extraction.lines) == len(truth.lines) + len(_doc(case).gap_rows) > len(truth.lines)
    assert _scored(extraction, []) != _scored(truth, [])


@pytest.mark.parametrize("case", SHEETS)
def test_not_scored_lists_only_what_the_sheet_path_cannot_produce(case: Case) -> None:
    extraction, _ = _run(case)
    truth = _expected(case)
    gaps = _gap_texts(case)
    patched = extraction.model_copy(
        update={"lines": [line for line in extraction.lines if line.raw_text not in gaps]}
    )
    for path in _meta(case).not_scored:
        # Each not-scored path is a real difference: dropping it from the list would fail.
        others = [p for p in _meta(case).not_scored if p != path]
        if _scored(patched, others) == _scored(truth, others):
            # Allowed only when both are null there (an address part the sheet doesn't print).
            assert path.startswith("header.ship_to.") or path.startswith("header.bill_to."), path

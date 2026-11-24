"""Shared by the fixture tests: each committed source is parsed once per test session."""

import functools

from app.extraction.inputs import ParsedDocument, run_parsers
from evals.generate.cases import by_name
from evals.run import FIXTURES_DIR


@functools.cache
def parsed(name: str) -> ParsedDocument:
    """The committed source of case `name`, through the real parsers (in this process)."""
    case = by_name()[name]
    outcome = run_parsers((FIXTURES_DIR / name / case.source_name).read_bytes())
    assert isinstance(outcome, ParsedDocument), outcome
    return outcome


@functools.cache
def document_text(name: str) -> str | None:
    """The text a case's truth is checked against: the text layer (PDF, EML); for a scan,
    the text layer of the PDF it was rasterized from (same spec, same renderer); for a
    spreadsheet, every cell, one row per line. None only for a PDF without text."""
    from evals.generate import render_pdf
    from evals.generate.spec import ScanDoc

    case = by_name()[name]
    if isinstance(case.document, ScanDoc):
        base = run_parsers(render_pdf.render(case, case.document.pdf).data)
        assert isinstance(base, ParsedDocument)
        return base.text
    document = parsed(name)
    if document.table is not None:
        return "\n".join(
            " | ".join(c for c in row.cells if c is not None) for row in document.table.rows
        )
    return document.text

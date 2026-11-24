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

"""The committed eval fixtures (M1 PR 6): valid, synthetic, parseable, grounded, reproducible.

One test per property, parametrized over the generator's registry. Each source is parsed
once (`_parsed`, cached) with the real parsers (`run_parsers`, in this process).
"""

import functools
import re
from pathlib import Path

import pytest

from app.extraction.grounding import check_grounding
from app.extraction.inputs import ParsedDocument, run_parsers
from app.extraction.schema import PurchaseOrderExtraction
from evals.generate import expected, files, meta
from evals.generate.cases import CASES
from evals.generate.spec import Case, PdfDoc
from evals.run import FIXTURES_DIR

MAX_FILE_BYTES = 2 * 1024 * 1024
KINDS = {"pdf": "pdf", "png": "png", "jpg": "jpeg", "xlsx": "xlsx", "csv": "csv", "eml": "eml"}
NAMES = [case.name for case in CASES]
_PAGE_RE = re.compile(r"^\[page (\d+)\]$", re.MULTILINE)


def _cases(predicate: object = None) -> list[pytest.ParameterSet]:
    chosen = [c for c in CASES if predicate is None or predicate(c)]  # type: ignore[operator]
    return [pytest.param(c, id=c.name) for c in chosen]


def _folder(case: Case) -> Path:
    return FIXTURES_DIR / case.name


@functools.cache
def _parsed(name: str) -> ParsedDocument:
    case = next(c for c in CASES if c.name == name)
    outcome = run_parsers((_folder(case) / case.source_name).read_bytes())
    assert isinstance(outcome, ParsedDocument), outcome
    return outcome


def _expected(case: Case) -> PurchaseOrderExtraction:
    return expected.load((_folder(case) / files.EXPECTED_NAME).read_bytes())


def _meta(case: Case) -> meta.CaseMeta:
    return meta.load((_folder(case) / files.META_NAME).read_bytes())


def _fold(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


@pytest.mark.parametrize("case", _cases())
def test_each_case_directory_holds_exactly_its_three_files(case: Case) -> None:
    found = sorted(p.name for p in _folder(case).iterdir())
    assert found == sorted([case.source_name, files.EXPECTED_NAME, files.META_NAME])


@pytest.mark.parametrize("case", _cases())
def test_expected_is_valid_v1_and_canonical(case: Case) -> None:
    raw = (_folder(case) / files.EXPECTED_NAME).read_bytes()
    truth = expected.load(raw)

    assert expected.dump(truth) == raw
    assert truth.header_confidence == 1.0
    assert all(line.confidence == 1.0 for line in truth.lines)


@pytest.mark.parametrize("case", _cases())
def test_meta_is_synthetic_and_describes_the_case(case: Case) -> None:
    raw = (_folder(case) / files.META_NAME).read_bytes()
    data = meta.load(raw)

    assert meta.dump(data) == raw
    assert data.synthetic is True
    assert data.input_type == case.input_type
    assert data.group == case.group
    assert data.seed == case.seed
    assert data.sender_email == case.sender_email
    assert case.group in data.tags and case.input_type in data.tags


@pytest.mark.parametrize("case", _cases())
def test_source_parses_as_its_type_with_the_expected_warnings(case: Case) -> None:
    parsed = _parsed(case.name)
    data = _meta(case)

    assert parsed.kind == KINDS[case.input_type]
    assert parsed.page_count == data.pages
    assert list(parsed.warnings) == data.expected_input_warnings


@pytest.mark.parametrize("case", _cases())
def test_each_file_is_under_the_size_cap(case: Case) -> None:
    for path in _folder(case).iterdir():
        assert path.stat().st_size < MAX_FILE_BYTES, path.name


@pytest.mark.parametrize("case", _cases(lambda c: c.input_type in ("pdf", "eml")))
def test_expected_values_are_grounded_in_the_text(case: Case) -> None:
    parsed = _parsed(case.name)
    if parsed.text is None:
        pytest.skip("no text layer (a scan): grounding doesn't run")

    warnings = check_grounding(_expected(case), parsed.text)

    assert [(w.code, w.path) for w in warnings] == [
        (w.code, w.path) for w in _meta(case).expected_extraction_warnings
    ]


def _page_texts(text: str) -> dict[int, str]:
    parts = _PAGE_RE.split(text)
    return {int(number): body for number, body in zip(parts[1::2], parts[2::2], strict=True)}


@pytest.mark.parametrize("case", _cases(lambda c: isinstance(c.document, PdfDoc)))
def test_every_line_is_printed_on_its_page(case: Case) -> None:
    parsed = _parsed(case.name)
    assert parsed.text is not None
    pages = {number: _fold(body) for number, body in _page_texts(parsed.text).items()}
    for index, line in enumerate(_expected(case).lines):
        assert line.page is not None, index
        needle = line.customer_part_number or line.vendor_part_number or line.description
        assert needle is not None, index
        assert _fold(needle) in pages[line.page], (index, line.page)


@pytest.mark.parametrize("case", _cases())
def test_the_case_regenerates_to_the_committed_files(case: Case) -> None:
    # Per format (owner decision Q2): bytes, or decoded pixels, or ZIP entries.
    assert files.problems(files.build_files(case), FIXTURES_DIR) == []


def _text_values(truth: PurchaseOrderExtraction) -> list[tuple[str, str]]:
    """Every text value of the truth that must be printed: (path, value)."""
    h = truth.header
    found: list[tuple[str, str | None]] = [
        ("header.customer_name", h.customer_name),
        ("header.customer_po_number", h.customer_po_number),
        ("header.po_date_raw", h.po_date_raw),
        ("header.requested_ship_date_raw", h.requested_ship_date_raw),
        ("header.payment_terms", h.payment_terms),
        ("header.notes", h.notes),
    ]
    for name in ("ship_to", "bill_to"):
        address = getattr(h, name)
        if address is not None:
            found += [(f"header.{name}.raw_text", part) for part in address.raw_text.split("\n")]
            for part in ("name", "attention", "line1", "line2", "city", "state", "postal_code"):
                found.append((f"header.{name}.{part}", getattr(address, part)))
    for i, line in enumerate(truth.lines):
        for name in ("customer_part_number", "vendor_part_number", "description", "uom"):
            found.append((f"lines.{i}.{name}", getattr(line, name)))
        found.append((f"lines.{i}.raw_text", line.raw_text))
    return [(path, value) for path, value in found if value is not None]


@pytest.mark.parametrize("case", _cases(lambda c: c.input_type in ("pdf", "eml")))
def test_every_text_value_of_the_truth_is_printed(case: Case) -> None:
    # Grounding covers ids and numbers only; this covers names, descriptions, units, dates,
    # notes and address parts (whitespace-insensitive: PDF text layers re-space words).
    parsed = _parsed(case.name)
    if parsed.text is None:
        pytest.skip("no text layer (a scan)")
    text = _fold(parsed.text)
    missing = [path for path, value in _text_values(_expected(case)) if _fold(value) not in text]
    assert missing == []

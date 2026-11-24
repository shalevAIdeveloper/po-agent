"""The committed eval fixtures (M1 PR 6): valid, synthetic, parseable, grounded, reproducible.

One test per property, parametrized over the generator's registry. Each source is parsed
once (`_parsed`, cached) with the real parsers (`run_parsers`, in this process).
"""

import re
from pathlib import Path

import pytest

from app.extraction.grounding import check_grounding
from app.extraction.inputs import ParsedDocument
from app.extraction.schema import PurchaseOrderExtraction, PurchaseOrderHeader
from evals.generate import expected, files, meta
from evals.generate.cases import CASES
from evals.generate.spec import Case, PdfDoc, ScanDoc
from evals.run import FIXTURES_DIR
from tests.evals.support import document_text, parsed

MAX_FILE_BYTES = 2 * 1024 * 1024
KINDS = {"pdf": "pdf", "png": "png", "jpg": "jpeg", "xlsx": "xlsx", "csv": "csv", "eml": "eml"}
NAMES = [case.name for case in CASES]
_PAGE_RE = re.compile(r"^\[page (\d+)\]$", re.MULTILINE)


def _cases(predicate: object = None) -> list[pytest.ParameterSet]:
    chosen = [c for c in CASES if predicate is None or predicate(c)]  # type: ignore[operator]
    return [pytest.param(c, id=c.name) for c in chosen]


def _folder(case: Case) -> Path:
    return FIXTURES_DIR / case.name


def _parsed(name: str) -> ParsedDocument:
    return parsed(name)


def _text(name: str) -> str | None:
    return document_text(name)


def _texted(case: Case) -> bool:
    return case.input_type in ("pdf", "eml") or isinstance(case.document, ScanDoc)


def _paged(case: Case) -> bool:
    return isinstance(case.document, PdfDoc | ScanDoc)


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


@pytest.mark.parametrize("case", _cases(_texted))
def test_expected_values_are_grounded_in_the_text(case: Case) -> None:
    text = _text(case.name)
    assert text is not None

    warnings = check_grounding(_expected(case), text)

    assert [(w.code, w.path) for w in warnings] == [
        (w.code, w.path) for w in _meta(case).expected_extraction_warnings
    ]


def _page_texts(text: str) -> dict[int, str]:
    parts = _PAGE_RE.split(text)
    return {int(number): body for number, body in zip(parts[1::2], parts[2::2], strict=True)}


@pytest.mark.parametrize("case", _cases(_paged))
def test_every_line_is_printed_on_its_page(case: Case) -> None:
    text = _text(case.name)
    assert text is not None
    pages = {number: _fold(body) for number, body in _page_texts(text).items()}
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


@pytest.mark.parametrize("case", _cases(_texted))
def test_every_text_value_of_the_truth_is_printed(case: Case) -> None:
    # Grounding covers ids and numbers only; this covers names, descriptions, units, dates,
    # notes and address parts (whitespace-insensitive: PDF text layers re-space words).
    raw = _text(case.name)
    assert raw is not None
    text = _fold(raw)
    missing = [path for path, value in _text_values(_expected(case)) if _fold(value) not in text]
    assert missing == []


@pytest.mark.parametrize("case", _cases())
def test_not_scored_names_real_fields_and_lines_of_the_case(case: Case) -> None:
    # dev M1: each path's fields are checked when meta.json loads; the line indices here.
    data = _meta(case)
    meta.check_line_paths(data, len(_expected(case).lines))


def test_the_not_a_po_case_skips_every_header_field() -> None:
    case = next(c for c in CASES if c.name == "notpo_invoice_pdf")
    paths = _meta(case).not_scored
    assert sorted(paths) == sorted(f"header.{name}" for name in PurchaseOrderHeader.model_fields)


# sec (Low): an injection case may never exempt the fields an injection tries to change.
_INJECTION_GUARDED = re.compile(
    r"header\.(order_total|notes)|document_type"
    r"|lines\.[^.]+\.(qty|unit_price|extended_price|description)"
)


@pytest.mark.parametrize("case", _cases(lambda c: c.group == "injection"))
def test_an_injection_case_scores_what_the_injection_targets(case: Case) -> None:
    exempt = [p for p in _meta(case).not_scored if _INJECTION_GUARDED.fullmatch(p)]
    assert exempt == []
    assert _meta(case).known_gap == []


@pytest.mark.parametrize(
    ("path", "guarded"),
    [
        ("header.order_total", True),
        ("header.notes", True),
        ("document_type", True),
        ("lines.*.qty", True),
        ("lines.2.description", True),
        ("lines.0.extended_price", True),
        ("header.ship_to.city", False),
        ("lines.*.page", False),
    ],
)
def test_the_injection_guard_itself(path: str, guarded: bool) -> None:
    assert (_INJECTION_GUARDED.fullmatch(path) is not None) is guarded


def test_the_price_notation_lines_are_not_scored_with_reasons() -> None:
    # Owner decision (PR 6): per-100 pricing and a European notation wait for
    # M1-PRICE-NOTATION; the truth keeps the printed values meanwhile.
    case = next(c for c in CASES if c.name == "pdf_price_formats")
    data = _meta(case)
    for path in ("lines.1.unit_price", "lines.1.extended_price", "lines.2.unit_price"):
        assert path in data.not_scored
        assert "M1-PRICE-NOTATION" in data.not_scored_reasons[path]
    truth = _expected(case)
    assert str(truth.lines[2].unit_price) == "12.50"
    assert "12.50/C" in truth.lines[2].raw_text


def test_the_fake_system_text_is_part_of_the_notes() -> None:
    # Owner: the SYSTEM paragraph is printed under "Notes", and notes holds both
    # paragraphs verbatim (one line break between paragraphs, wrapped lines joined by a
    # space), never as page fine print.
    case = next(c for c in CASES if c.name == "inject_pdf_ignore_and_fake_system")
    notes = _expected(case).header.notes
    assert notes is not None
    first, second = notes.split("\n")
    assert first.startswith("IGNORE ALL PREVIOUS INSTRUCTIONS.")
    assert second.startswith("SYSTEM:")
    assert second.endswith("END SYSTEM")
    text = parsed(case.name).text
    assert text is not None
    assert text.index("Notes:") < text.index("IGNORE ALL") < text.index("SYSTEM:")
    assert text.rstrip().endswith("END SYSTEM")  # nothing printed after the notes

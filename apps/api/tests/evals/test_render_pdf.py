"""The PDF renderer (M1 PR 6 T2): deterministic, parseable, and pages as the spec says."""

import random
import re
from typing import Any

import pypdfium2 as pdfium
import pytest

from app.extraction.inputs import ParsedDocument, run_parsers
from evals.generate import render_pdf
from evals.generate.cases.common import (
    STANDARD,
    letterhead,
    office,
    products,
    site,
    total_num,
    when,
)
from evals.generate.data import BRINDLEWOOD
from evals.generate.spec import Block, Case, Header, Line, PdfDoc, PdfLayout, money

LAYOUTS: tuple[PdfLayout, ...] = ("grid", "nogrid", "two_column", "landscape", "letterhead")


def sample(layout: PdfLayout = "grid", count: int = 6, **doc: Any) -> Case:
    lines = tuple(products(random.Random(11), BRINDLEWOOD, count))
    return Case(
        name=f"sample_{layout}",
        group="clean_pdf",
        seed=11,
        header=Header(
            customer_name=BRINDLEWOOD.name,
            po_number="4500098765",
            ship_to=site(BRINDLEWOOD, 0),
            bill_to=office(BRINDLEWOOD),
            order_total=total_num(lines),
            payment_terms="Net 30",
            notes="Deliver between 7 am and 3 pm.",
        ),
        lines=lines,
        document=PdfDoc(
            layout,
            STANDARD,
            letterhead=letterhead(BRINDLEWOOD),
            totals=(("Total", money(total_num(lines).value or 0)),),
            **doc,
        ),
        sender_email=BRINDLEWOOD.buyer_email,
        received_at=when(2),
    )


def page_texts(data: bytes) -> list[str]:
    pdf = pdfium.PdfDocument(data)
    try:
        texts = []
        for page in pdf:
            textpage = page.get_textpage()
            texts.append(textpage.get_text_bounded())
            textpage.close()
            page.close()
        return texts
    finally:
        pdf.close()


def _render(case: Case) -> render_pdf.PdfResult:
    assert isinstance(case.document, PdfDoc)
    return render_pdf.render(case, case.document)


@pytest.mark.parametrize("layout", LAYOUTS)
def test_every_layout_renders_the_same_bytes_twice(layout: PdfLayout) -> None:
    case = sample(layout)
    first, second = _render(case), _render(case)

    assert first.data == second.data
    assert first.data.startswith(b"%PDF-")
    assert b"/CreationDate" in first.data  # invariant=True: a fixed date, not the clock


@pytest.mark.parametrize("layout", LAYOUTS)
def test_every_layout_parses_as_a_text_pdf_with_every_value(layout: PdfLayout) -> None:
    case = sample(layout)
    result = _render(case)
    parsed = run_parsers(result.data)

    assert isinstance(parsed, ParsedDocument)
    assert parsed.kind == "pdf"
    assert parsed.page_count == result.pages == 1
    assert parsed.text is not None
    folded = re.sub(r"\s+", "", parsed.text)
    assert "4500098765" in folded
    for line in case.lines:
        for value in (line.cpn, line.vpn, line.qty, line.price, line.ext):
            printed = value if isinstance(value, str) else value and value.printed
            assert printed and re.sub(r"\s+", "", printed) in folded, (layout, printed)


def test_pages_follow_the_page_breaks_and_lines_land_on_their_page() -> None:
    case = sample(
        "grid",
        30,
        page_breaks=(12, 25),
        continued=True,
        page_subtotals=True,
        blocks=(Block(("Terms apply.",) * 3, where="terms_page", title="Terms"),),
    )
    result = _render(case)
    texts = page_texts(result.data)

    assert result.pages == len(texts) == 4  # three table pages and the terms page
    assert result.line_pages == (1,) * 12 + (2,) * 13 + (3,) * 5
    for line, page in zip(case.lines, result.line_pages, strict=True):
        assert line.cpn is not None
        assert line.cpn in texts[page - 1]
        assert all(line.cpn not in texts[other] for other in range(4) if other != page - 1)
    assert "Continued on page 2" in texts[0]
    assert "Page subtotal" in texts[0]
    assert "Page 4 of 4" in texts[3]
    assert "Terms apply." in texts[3]


def test_an_overflowing_page_is_an_error_not_a_reflow() -> None:
    with pytest.raises(ValueError, match="page overflow"):
        _render(sample("grid", 60))


@pytest.mark.parametrize("breaks", [(0,), (5, 5), (7, 3), (30,)])
def test_bad_page_breaks_are_refused(breaks: tuple[int, ...]) -> None:
    with pytest.raises(ValueError, match="page_breaks"):
        _render(sample("grid", 10, page_breaks=breaks))


def test_a_cell_that_does_not_fit_its_column_is_an_error() -> None:
    case = sample()
    wide = Line(cpn="X" * 40, raw="x")
    with pytest.raises(ValueError, match="doesn't fit"):
        _render(
            Case(**{**{f: getattr(case, f) for f in case.__dataclass_fields__}, "lines": (wide,)})
        )


def test_a_long_description_wraps_and_stays_whole_in_the_text_layer() -> None:
    long = "Wire rope assembly, galvanized, with thimble eyes both ends and a tag " * 2
    case = sample()
    lines = (Line(no="1", cpn="BFC-1", desc=long.strip(), qty=case.lines[0].qty),)
    changed = Case(**{**{f: getattr(case, f) for f in case.__dataclass_fields__}, "lines": lines})
    text = re.sub(r"\s+", " ", page_texts(_render(changed).data)[0])

    assert long.strip() in text

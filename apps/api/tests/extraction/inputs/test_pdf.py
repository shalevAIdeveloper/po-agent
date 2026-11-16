import io
import math

import pytest
from PIL import Image
from reportlab.lib import pdfencrypt

from app.extraction.inputs import parse_document
from app.extraction.inputs import pdf as pdf_module
from app.extraction.inputs.errors import InputError
from app.extraction.inputs.limits import MAX_IMAGE_SIDE, MAX_PDF_PAGES
from app.extraction.inputs.pdf import parse_pdf, render_scale
from tests.extraction.inputs.files import make_pdf, make_scanned_pdf

PO_PAGE = "PURCHASE ORDER PO# 4500012345\n1 00123-A Widget, blue 12 CS 1,234.50"


def code_of(data: bytes) -> str:
    with pytest.raises(InputError) as caught:
        parse_document(data)
    return caught.value.code


def test_text_pdf_gives_page_text_and_one_image_per_page() -> None:
    parsed = parse_pdf(make_pdf([PO_PAGE, "Page two: 2 BX-77 Bolt kit 1 EA .75"]))
    assert parsed.page_count == 2
    assert parsed.text is not None
    assert parsed.text.startswith("[page 1]\nPURCHASE ORDER PO# 4500012345\n")
    assert "\n\n[page 2]\nPage two: 2 BX-77 Bolt kit" in parsed.text
    assert "\r" not in parsed.text
    assert len(parsed.images) == 2
    for part in parsed.images:
        image = Image.open(io.BytesIO(part.data))
        assert part.media_type in ("image/png", "image/jpeg")
        assert max(image.size) <= MAX_IMAGE_SIDE


def test_a_letter_page_renders_at_150_dpi_capped_to_the_long_side() -> None:
    (part,) = parse_pdf(make_pdf([PO_PAGE])).images
    # 8.5 x 11 in at 150 dpi = 1275 x 1650, capped to 1568 on the long side.
    width, height = Image.open(io.BytesIO(part.data)).size
    assert height == MAX_IMAGE_SIDE
    assert abs(width - 612 * MAX_IMAGE_SIDE / 792) <= 1


def test_a_huge_page_box_renders_small() -> None:
    # 200 x 200 inches: at 150 dpi that would be a 900-megapixel bitmap.
    (part,) = parse_pdf(make_pdf(["x"], pagesize=(14_400.0, 14_400.0))).images
    assert Image.open(io.BytesIO(part.data)).size == (MAX_IMAGE_SIDE, MAX_IMAGE_SIDE)


@pytest.mark.parametrize(("width", "height"), [(0, 100), (100, -1), (math.nan, 100)])
def test_a_page_without_a_size_is_invalid(width: float, height: float) -> None:
    with pytest.raises(InputError, match="invalid_file"):
        render_scale(width, height)


def test_a_scanned_pdf_has_no_text_layer() -> None:
    parsed = parse_pdf(make_scanned_pdf())
    assert parsed.text is None
    assert len(parsed.images) == 1


def test_too_many_pages_is_refused_before_rendering(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_render(*_args: object) -> None:
        raise AssertionError("a page was loaded")

    monkeypatch.setattr(pdf_module, "_page", no_render)
    assert code_of(make_pdf(["p"] * (MAX_PDF_PAGES + 1))) == "too_many_pages"


def test_the_page_cap_is_inclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pdf_module, "MAX_PDF_PAGES", 2)
    assert parse_pdf(make_pdf([PO_PAGE, PO_PAGE])).page_count == 2
    with pytest.raises(InputError, match="too_many_pages"):
        parse_pdf(make_pdf([PO_PAGE] * 3))


def test_a_pdf_that_needs_a_password_is_unsupported() -> None:
    locked = make_pdf([PO_PAGE], encrypt=pdfencrypt.StandardEncryption("user", "owner"))
    assert code_of(locked) == "unsupported_pdf"


def test_a_pdf_with_only_an_owner_password_is_read() -> None:
    # Owner, 2026-11-16: opens without a password (print/copy restrictions only).
    restricted = pdfencrypt.StandardEncryption("", ownerPassword="owner", canPrint=0, canCopy=0)
    parsed = parse_pdf(make_pdf([PO_PAGE], encrypt=restricted))
    assert parsed.text is not None
    assert "4500012345" in parsed.text


@pytest.mark.parametrize("data", [b"%PDF-1.7\n" + b"garbage" * 20, make_pdf([PO_PAGE])[:200]])
def test_a_broken_pdf_is_an_invalid_file(data: bytes) -> None:
    assert code_of(data) == "invalid_file"


def test_parse_document_routes_pdfs() -> None:
    document = parse_document(make_pdf([PO_PAGE]))
    assert (document.kind, document.page_count, document.warnings) == ("pdf", 1, ())
    assert document.text is not None
    assert "00123-A" in document.text

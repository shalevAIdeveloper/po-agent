import io
import math

import pypdfium2 as pdfium
import pytest
from PIL import Image
from reportlab.lib import pdfencrypt
from reportlab.pdfgen import canvas

from app.extraction.inputs import parse_document
from app.extraction.inputs import pdf as pdf_module
from app.extraction.inputs.errors import InputError
from app.extraction.inputs.limits import MAX_IMAGE_SIDE, MAX_PDF_PAGES
from app.extraction.inputs.pdf import parse_pdf, render_scale
from tests.extraction.inputs.files import LETTER, make_pdf, make_scanned_pdf

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


def test_form_field_values_are_drawn_but_not_in_the_text_layer() -> None:
    # `qa`: init_forms + may_draw_forms; the text-layer gap is the ADR's known false alarm.
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=LETTER, invariant=True)
    pdf.drawString(72, 720, "PURCHASE ORDER 4500012345 with a filled form field below")
    pdf.acroForm.textfield(
        name="qty",
        value="FORMVALUE 12",
        x=72,
        y=600,
        width=300,
        height=30,
        borderWidth=0,
        fillColor=None,
        fontSize=18,
    )
    pdf.showPage()
    pdf.save()
    parsed = parse_pdf(buffer.getvalue())
    assert "FORMVALUE" not in (parsed.text or "")
    image = Image.open(io.BytesIO(parsed.images[0].data)).convert("L")
    # The field box in PDF points (origin bottom-left) is x 72..372, y 600..630.
    scale = image.size[1] / LETTER[1]
    top, bottom = (LETTER[1] - 630) * scale, (LETTER[1] - 600) * scale
    field = image.crop((int(72 * scale), int(top), int(372 * scale), int(bottom)))
    assert sum(field.histogram()[:128]) > 50  # dark pixels: the value was drawn


def test_the_scan_threshold_counts_non_space_characters() -> None:
    below = "a b c d e f g h i j k l m n o p q r s"  # 19 characters, many spaces
    assert parse_pdf(make_pdf([below])).text is None
    assert parse_pdf(make_pdf([below + " t"])).text is not None


def test_a_small_page_is_not_scaled_up() -> None:
    (part,) = parse_pdf(make_pdf(["x"], pagesize=(216.0, 216.0))).images  # 3 x 3 in
    width, height = Image.open(io.BytesIO(part.data)).size
    assert abs(width - 450) <= 1  # 150 dpi (PDFium rounds up), not 1568
    assert abs(height - 450) <= 1


def test_a_pdf_without_pages_is_refused() -> None:
    # PDFium itself refuses to open it (format error); `parse_pdf`'s page_count == 0
    # check stays as a backstop for a PDFium that would open it.
    document = pdfium.PdfDocument.new()
    buffer = io.BytesIO()
    document.save(buffer)
    document.close()
    assert code_of(buffer.getvalue()) == "invalid_file"


def test_too_much_text_stops_before_the_next_page_is_rendered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered: list[int] = []
    real_scale = pdf_module.render_scale

    def counting_scale(width: float, height: float) -> float:
        rendered.append(1)
        return real_scale(width, height)

    monkeypatch.setattr(pdf_module, "render_scale", counting_scale)
    monkeypatch.setattr(pdf_module, "MAX_TEXT_CHARS", 80)
    with pytest.raises(InputError, match="document_too_large"):
        parse_pdf(make_pdf([PO_PAGE, PO_PAGE, PO_PAGE]))
    assert len(rendered) == 1  # page 1 rendered; page 2's text went over the cap


def test_parse_document_routes_pdfs() -> None:
    document = parse_document(make_pdf([PO_PAGE]))
    assert (document.kind, document.page_count, document.warnings) == ("pdf", 1, ())
    assert document.text is not None
    assert "00123-A" in document.text

"""PDF with pypdfium2 (PDFium, as built by pypdfium2: no JavaScript, no XFA).

- At most MAX_PDF_PAGES pages (Q12), checked before any page is loaded.
- A PDF that needs a password to open is `unsupported_pdf`. One protected only by an owner
  password (no printing / copying) opens without one and is read normally (owner,
  2026-11-16): ERP exports often look like that.
- Text layer per page, each under a `[page N]` line (the model fills the line's `page` from
  it). Fewer than MIN_TEXT_CHARS non-space characters in the whole file = a scan: no text,
  so grounding is skipped (ADR 0011).
- Every page is also rendered (forms included) at PDF_RENDER_DPI, never more than
  MAX_IMAGE_SIDE on its long side, so a huge page box can't allocate a huge bitmap.
- Values typed into form fields are drawn on the page image but are not in the text
  layer, so grounding may flag them (a false alarm the reviewer can see; ADR 0012).
- Text can be in the text layer without being visible (render mode 3, white text): OCR'd
  scans rely on it, so it is kept; the review screen shows the text the model read
  (`sec` M3, ADR 0012).
- The text is checked against MAX_TEXT_CHARS page by page, before each page is rendered.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Final

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw

from app.extraction.inputs.errors import InputError
from app.extraction.inputs.image import encode_for_model
from app.extraction.inputs.limits import (
    MAX_IMAGE_SIDE,
    MAX_PDF_PAGES,
    MAX_TEXT_CHARS,
    MIN_TEXT_CHARS,
    PDF_RENDER_DPI,
)
from app.llm.types import ImagePart

POINTS_PER_INCH: Final = 72
_PASSWORD_ERRORS: Final = frozenset({pdfium_raw.FPDF_ERR_PASSWORD, pdfium_raw.FPDF_ERR_SECURITY})


@dataclass(frozen=True, slots=True)
class ParsedPdf:
    text: str | None
    images: tuple[ImagePart, ...]
    page_count: int


@contextmanager
def _open(data: bytes) -> Iterator[pdfium.PdfDocument]:
    try:
        document = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as error:
        code = getattr(error, "err_code", None)
        raise InputError(
            "unsupported_pdf" if code in _PASSWORD_ERRORS else "invalid_file"
        ) from None
    try:
        yield document
    finally:
        document.close()


def render_scale(width: float, height: float) -> float:
    """PDF_RENDER_DPI, lowered so the long side stays within MAX_IMAGE_SIDE pixels."""
    if not (width > 0 and height > 0):
        raise InputError("invalid_file")
    return min(PDF_RENDER_DPI / POINTS_PER_INCH, MAX_IMAGE_SIDE / max(width, height))


def _page(document: pdfium.PdfDocument, index: int, text_budget: int) -> tuple[str, ImagePart]:
    page = document[index]
    try:
        textpage = page.get_textpage()
        try:
            text = textpage.get_text_bounded()
        finally:
            textpage.close()
        if len(text) > text_budget:
            # Stop before rendering: a page full of text operators is slow to draw (`sec` L3).
            raise InputError("document_too_large")
        width, height = page.get_size()
        bitmap = page.render(scale=render_scale(width, height), may_draw_forms=True)
        try:
            image = encode_for_model(bitmap.to_pil())
        finally:
            bitmap.close()
    finally:
        page.close()
    return text.replace("\r\n", "\n").replace("\r", "\n").strip(), image


def parse_pdf(data: bytes) -> ParsedPdf:
    with _open(data) as document:
        page_count = len(document)
        if page_count == 0:
            raise InputError("empty_document")
        if page_count > MAX_PDF_PAGES:
            raise InputError("too_many_pages")
        document.init_forms()
        texts: list[str] = []
        images: list[ImagePart] = []
        used = 0
        for index in range(page_count):
            text, image = _page(document, index, MAX_TEXT_CHARS - used)
            used += len(text)
            texts.append(text)
            images.append(image)
    visible = sum(len("".join(text.split())) for text in texts)
    joined = "\n\n".join(f"[page {number}]\n{text}" for number, text in enumerate(texts, 1))
    return ParsedPdf(
        text=joined if visible >= MIN_TEXT_CHARS else None,
        images=tuple(images),
        page_count=page_count,
    )

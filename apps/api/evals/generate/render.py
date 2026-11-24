"""Renders a case's document: bytes, page count, and the page each line is printed on."""

from dataclasses import dataclass

from evals.generate.spec import Case, EmailDoc, PdfDoc, ScanDoc


@dataclass(frozen=True, slots=True)
class Rendered:
    source: bytes
    pages: int
    line_pages: tuple[int | None, ...]  # one per line: PDF page, 1 for an image, else None


def render(case: Case) -> Rendered:
    # Renderers are imported when needed: each pulls in reportlab / pypdfium2 / openpyxl.
    document = case.document
    if isinstance(document, PdfDoc):
        from evals.generate import render_pdf

        result = render_pdf.render(case, document)
        return Rendered(result.data, result.pages, result.line_pages)
    if isinstance(document, ScanDoc):
        from evals.generate import render_scan

        return render_scan.render(case, document)
    if isinstance(document, EmailDoc):
        from evals.generate import render_email

        return Rendered(render_email.render(case, document), 1, (None,) * len(case.lines))
    raise NotImplementedError(type(document).__name__)

"""Renders a case's document: bytes, page count, and the page each line is printed on."""

from dataclasses import dataclass

from evals.generate.spec import Case


@dataclass(frozen=True, slots=True)
class Rendered:
    source: bytes
    pages: int
    line_pages: tuple[int | None, ...]  # one per line: PDF page, 1 for an image, else None


def render(case: Case) -> Rendered:
    # Renderers are imported when needed: each pulls in reportlab / pypdfium2 / openpyxl.
    raise NotImplementedError(type(case.document).__name__)

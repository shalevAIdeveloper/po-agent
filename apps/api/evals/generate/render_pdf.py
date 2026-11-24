"""Text PDFs with reportlab: five layouts, pagination, and the page of every line.

Deterministic: `invariant=True` (fixed creation date and document ID), no page
compression, fixed metadata, and an embedded font: Bitstream Vera, which ships inside
reportlab. Not the standard Helvetica: a viewer substitutes a font of its own for a
non-embedded standard font, and PDFium does so differently on Windows and Linux, so the
same PDF rasterized to different pixels there (the scans, and the page images the parser
sends the model). With the font embedded, both platforms draw the same glyphs. Pagination is the
spec's `page_breaks` (truth by construction); a page that overflows is an error, never a
silent reflow. Every value the spec prints is drawn whole: only descriptions and free
text wrap, and a cell that doesn't fit its column is an error.
"""

import io
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import reportlab
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from evals.generate import GENERATOR
from evals.generate.data import DISTRIBUTOR, Party
from evals.generate.spec import (
    AddressBlock,
    Block,
    Case,
    Col,
    Line,
    PdfDoc,
    money,
)

FONT: Final = "Vera"
BOLD: Final = "VeraBd"
_FONT_DIR: Final = Path(reportlab.__file__).resolve().parent / "fonts"
for _name in (FONT, BOLD):
    if _name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(_name, str(_FONT_DIR / f"{_name}.ttf")))
SIZE: Final = 9.0
LEADING: Final = 11.5
MARGIN: Final = 40.0
BOTTOM: Final = 54.0  # the page footer ("Page 1 of 2") sits below this
GRAY: Final = 0.88
PARAGRAPH_GAP: Final = 4.0  # between paragraphs of one text block (e.g. notes)


@dataclass(frozen=True, slots=True)
class PdfResult:
    data: bytes
    pages: int
    line_pages: tuple[int | None, ...]


def page_groups(doc: PdfDoc, count: int) -> list[range]:
    """Line indices per table page, from the spec's page breaks."""
    starts = [0, *doc.page_breaks]
    if starts != sorted(set(starts)) or any(not 0 < b < count for b in doc.page_breaks):
        raise ValueError("page_breaks must increase strictly and fall inside the lines")
    ends = [*doc.page_breaks, count]
    return [range(a, b) for a, b in zip(starts, ends, strict=True)]


def table_pages(doc: PdfDoc, count: int) -> int:
    return len(page_groups(doc, count))


def line_pages(doc: PdfDoc, count: int) -> tuple[int, ...]:
    pages = [0] * count
    for number, group in enumerate(page_groups(doc, count), start=1):
        for index in group:
            pages[index] = number
    return tuple(pages)


class _Pen:
    """A canvas with a cursor, overflow checks and the few drawing helpers we need."""

    def __init__(self, pdf: Any, size: tuple[float, float]) -> None:
        self.pdf = pdf
        self.width, self.height = size
        self.y = self.height - MARGIN

    @property
    def right(self) -> float:
        return self.width - MARGIN

    def need(self, height: float) -> None:
        if self.y - height < BOTTOM:
            raise ValueError("page overflow: move a page break earlier")

    def text(self, x: float, y: float, value: str, *, font: str = FONT, size: float = SIZE) -> None:
        self.pdf.setFont(font, size)
        self.pdf.drawString(x, y, value)

    def text_right(
        self, x: float, y: float, value: str, *, font: str = FONT, size: float = SIZE
    ) -> None:
        self.pdf.setFont(font, size)
        self.pdf.drawRightString(x, y, value)

    def centered(self, y: float, value: str, *, font: str = FONT, size: float = SIZE) -> None:
        self.pdf.setFont(font, size)
        self.pdf.drawCentredString(self.width / 2, y, value)

    def wrap(self, value: str, width: float, *, font: str = FONT, size: float = SIZE) -> list[str]:
        out: list[str] = []
        for paragraph in value.split("\n"):
            out += simpleSplit(paragraph, font, size, width) or [""]
        return out

    def paragraph(
        self, x: float, value: str, width: float, *, font: str = FONT, size: float = SIZE
    ) -> None:
        """Paragraphs split at line breaks, each wrapped to `width`, a small gap between them."""
        leading = size + 2.5
        for number, part in enumerate(value.split("\n")):
            if number:
                self.y -= PARAGRAPH_GAP
            for line in self.wrap(part, width, font=font, size=size):
                self.need(leading)
                self.y -= leading
                self.text(x, self.y, line, font=font, size=size)

    def rule(self, y: float, x0: float | None = None, x1: float | None = None) -> None:
        self.pdf.setLineWidth(0.6)
        self.pdf.line(MARGIN if x0 is None else x0, y, self.right if x1 is None else x1, y)


def _fits(value: str, width: float, font: str = FONT, size: float = SIZE) -> bool:
    return bool(stringWidth(value, font, size) <= width)


def party_lines(party: Party) -> tuple[str, ...]:
    """Name, street, city line, phone: a letterhead or the vendor block."""
    return (party.name, party.street, party.city_line, party.phone)


class _Renderer:
    def __init__(self, case: Case, doc: PdfDoc) -> None:
        self.case = case
        self.doc = doc
        self.size = landscape(letter) if doc.layout == "landscape" else letter
        self.buffer = io.BytesIO()
        self.pdf = canvas.Canvas(self.buffer, pagesize=self.size, invariant=True, pageCompression=0)
        self.pdf.setAuthor(GENERATOR)
        self.pdf.setCreator(GENERATOR)
        self.pdf.setTitle(f"eval case {case.name}")
        self.pdf.setSubject("synthetic eval fixture")
        self.pen = _Pen(self.pdf, self.size)
        # The table's rows: the order lines, or (a document that isn't an order) rows that
        # are printed but are not lines of the truth.
        self.rows = case.lines if doc.table_lines is None else doc.table_lines
        if doc.table_lines is not None and case.lines:
            raise ValueError(f"{case.name}: table_lines replace the lines, never both")
        self.groups = page_groups(doc, len(self.rows))
        terms = [b for b in doc.blocks if b.where == "terms_page"]
        self.total_pages = len(self.groups) + (1 if terms else 0)
        self.page = 1
        self._check_columns()

    # --- checks ------------------------------------------------------------------------

    def _check_columns(self) -> None:
        width = sum(col.width for col in self.doc.columns)
        if width > self.pen.right - MARGIN + 0.01:
            raise ValueError(f"{self.case.name}: columns are wider than the page")
        for col in self.doc.columns:
            if not _fits(col.heading, col.width - 6, BOLD):
                raise ValueError(
                    f"{self.case.name}: heading {col.heading!r} doesn't fit its column"
                )
        for line in self.rows:
            for col in self.doc.columns:
                cell = line.cell(col.key)
                if cell and col.key != "desc" and not _fits(cell, col.width - 6):
                    raise ValueError(f"{self.case.name}: {cell!r} doesn't fit column {col.key}")

    # --- page furniture ------------------------------------------------------------------

    def _issuer_lines(self) -> tuple[str, ...]:
        name = self.doc.issuer or self.case.header.customer_name
        return ((name,) if name else ()) + self.doc.letterhead

    def _footer(self) -> None:
        if self.total_pages > 1:
            self.pen.centered(30, f"Page {self.page} of {self.total_pages}", size=8)

    def _new_page(self) -> None:
        self._footer()
        self.pdf.showPage()
        self.page += 1
        self.pen.y = self.pen.height - MARGIN

    def _fields(self) -> list[tuple[str, str]]:
        h, doc = self.case.header, self.doc
        fields: list[tuple[str, str]] = []
        if h.po_number:
            fields.append((doc.po_label, h.po_number))
        if h.po_date:
            fields.append((doc.date_label, h.po_date.printed))
        if h.ship_date:
            fields.append((doc.ship_date_label, h.ship_date.printed))
        if h.payment_terms:
            fields.append((doc.terms_label, h.payment_terms))
        if h.currency:
            fields.append(("Currency", h.currency))
        return fields + list(doc.fields)

    def _field_rows(self, x: float, y: float, fields: Sequence[tuple[str, str]]) -> float:
        label_width = max((stringWidth(f"{k}:", BOLD, SIZE) for k, _ in fields), default=0) + 6
        for label, value in fields:
            y -= LEADING
            self.pen.text(x, y, f"{label}:", font=BOLD)
            self.pen.text(x + label_width, y, value)
        return y

    def _address(self, x: float, y: float, title: str, lines: Sequence[str]) -> float:
        self.pen.text(x, y, title, font=BOLD)
        for line in lines:
            y -= LEADING
            self.pen.text(x, y, line)
        return y

    def _blocks(self, where: str) -> list[Block]:
        return [b for b in self.doc.blocks if b.where == where]

    def _addresses(self) -> list[tuple[str, tuple[str, ...]]]:
        h = self.case.header
        found: list[tuple[str, tuple[str, ...]]] = []
        if self.doc.show_vendor:
            found.append(("Vendor:", party_lines(DISTRIBUTOR)[:3]))
        ship: AddressBlock | None = h.ship_to
        bill: AddressBlock | None = h.bill_to
        if ship is not None:
            found.append(("Ship To:", ship.lines))
        if bill is not None:
            found.append(("Bill To:", bill.lines))
        return found

    # --- the first page header, per layout ----------------------------------------------

    def _top(self) -> None:
        pen, doc = self.pen, self.doc
        issuer = self._issuer_lines()
        top = pen.y
        if doc.layout == "letterhead":
            if issuer:
                pen.centered(top - 18, issuer[0], font=BOLD, size=20)
                pen.centered(top - 32, "  |  ".join(issuer[1:]), size=8)
            y = top - 34
            for block in self._blocks("top"):
                for line in block.lines:
                    y -= block.size + 2.5
                    pen.centered(y, line, size=block.size)
            pen.rule(y - 6)
            pen.centered(y - 26, doc.title, font=BOLD, size=16)
            y = self._field_rows(MARGIN, y - 34, self._fields())
            pen.y = self._address_row(y - 22)
            return
        # grid, nogrid, landscape, two_column: issuer top left, title top right.
        y = top - 14
        if issuer:
            pen.text(MARGIN, y, issuer[0], font=BOLD, size=14)
            for line in issuer[1:]:
                y -= LEADING
                pen.text(MARGIN, y, line)
        for block in self._blocks("top"):
            for line in block.lines:
                y -= block.size + 2.5
                pen.text(MARGIN, y, line, size=block.size)
        pen.text_right(pen.right, top - 16, doc.title, font=BOLD, size=18)
        fields_x = pen.right - 210
        if doc.layout == "two_column":
            # Left column: the addresses stacked; right column: the order facts.
            fy = self._field_rows(fields_x, top - 24, self._fields())
            ay = y - 16
            for title, lines in self._addresses():
                ay = self._address(MARGIN, ay, title, lines) - 16
            pen.y = min(ay + 8, fy) - 10
            return
        fy = self._field_rows(fields_x, top - 24, self._fields())
        pen.y = self._address_row(min(y, fy) - 18)

    def _address_row(self, y: float) -> float:
        blocks = self._addresses()
        if not blocks:
            return y
        column = (self.pen.right - MARGIN) / len(blocks)
        low = y
        for i, (title, lines) in enumerate(blocks):
            low = min(low, self._address(MARGIN + i * column, y, title, lines))
        return low - 14

    def _continuation_top(self) -> None:
        h = self.case.header
        label = f"{self.doc.title.title()} {h.po_number}" if h.po_number else self.doc.title.title()
        self.pen.text(MARGIN, self.pen.y - 12, f"{label} (continued)", font=BOLD, size=11)
        self.pen.y -= 30

    # --- the table -----------------------------------------------------------------------

    def _x(self) -> list[float]:
        xs, x = [], MARGIN
        for col in self.doc.columns:
            xs.append(x)
            x += col.width
        return xs

    def _row_lines(self, line: Line) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for col in self.doc.columns:
            cell = line.cell(col.key)
            if not cell:
                continue
            out[col.key] = self.pen.wrap(cell, col.width - 6) if col.key == "desc" else [cell]
        return out

    def _cell(self, col: Col, x: float, y: float, value: str, font: str = FONT) -> None:
        if col.align == "r":
            self.pen.text_right(x + col.width - 3, y, value, font=font)
        else:
            self.pen.text(x + 3, y, value, font=font)

    def _heading(self) -> None:
        pen, xs = self.pen, self._x()
        height = LEADING + 5
        pen.need(height)
        top = pen.y
        if self.doc.layout in ("grid", "landscape", "two_column", "letterhead"):
            self.pdf.setFillGray(GRAY)
            self.pdf.rect(
                MARGIN,
                top - height,
                sum(c.width for c in self.doc.columns),
                height,
                stroke=0,
                fill=1,
            )
            self.pdf.setFillGray(0)
        for col, x in zip(self.doc.columns, xs, strict=True):
            self._cell(col, x, top - LEADING + 1, col.heading, font=BOLD)
        pen.y = top - height
        if self._grid:
            self._grid_box(top, pen.y)
        else:
            pen.rule(pen.y, MARGIN, MARGIN + sum(c.width for c in self.doc.columns))

    @property
    def _grid(self) -> bool:
        return self.doc.layout in ("grid", "landscape")

    def _grid_box(self, top: float, bottom: float) -> None:
        xs = [*self._x(), MARGIN + sum(c.width for c in self.doc.columns)]
        self.pdf.setLineWidth(0.5)
        self.pdf.rect(MARGIN, bottom, xs[-1] - MARGIN, top - bottom, stroke=1, fill=0)
        for x in xs[1:-1]:
            self.pdf.line(x, bottom, x, top)

    def _row(self, line: Line) -> None:
        cells = self._row_lines(line)
        count = max((len(v) for v in cells.values()), default=1)
        height = count * LEADING + 4
        self.pen.need(height)
        top = self.pen.y
        for col, x in zip(self.doc.columns, self._x(), strict=True):
            for i, value in enumerate(cells.get(col.key, [])):
                self._cell(col, x, top - LEADING * (i + 1) + 1, value)
        self.pen.y = top - height
        if self._grid:
            self._grid_box(top, self.pen.y)

    def _label_row(self, label: str, amount: str, *, font: str = FONT) -> None:
        """A row in the amount column with its label to the left (subtotals, totals)."""
        cols = self.doc.columns
        self.pen.need(LEADING + 2)
        self.pen.y -= LEADING + 2
        right = MARGIN + sum(c.width for c in cols) - 3
        last = cols[-1].width
        self.pen.text_right(right, self.pen.y, amount, font=font)
        self.pen.text_right(right - last - 6, self.pen.y, label, font=font)

    # --- document ------------------------------------------------------------------------

    def _page_subtotal(self, group: range) -> str:
        total = Decimal(0)
        for index in group:
            ext = self.rows[index].ext
            if ext is not None and ext.value is not None:
                total += ext.value
        return money(total)

    def run(self) -> PdfResult:
        last = len(self.groups) - 1
        for number, group in enumerate(self.groups):
            if number == 0:
                self._top()
            else:
                self._continuation_top()
            if number == 0 or self.doc.repeat_header:
                self._heading()
            for index in group:
                self._row(self.rows[index])
            if self.doc.page_subtotals and number < last:
                self._label_row("Page subtotal:", self._page_subtotal(group))
            if number < last:
                if self.doc.continued:
                    self.pen.text_right(
                        self.pen.right, BOTTOM + 4, f"Continued on page {number + 2}", size=8
                    )
                self._new_page()
        self._end_of_table()
        terms = self._blocks("terms_page")
        if terms:
            self._new_page()
            for block in terms:
                if block.title:
                    self.pen.y -= 16
                    self.pen.text(MARGIN, self.pen.y, block.title, font=BOLD, size=12)
                    self.pen.y -= 4
                for line in block.lines:
                    self.pen.paragraph(MARGIN, line, self.pen.right - MARGIN, size=block.size)
                    self.pen.y -= 4
        self._footer()
        self.pdf.showPage()
        self.pdf.save()
        return PdfResult(
            data=self.buffer.getvalue(),
            pages=self.total_pages,
            line_pages=line_pages(self.doc, len(self.case.lines)),
        )

    def _end_of_table(self) -> None:
        pen = self.pen
        if self.doc.page_subtotals and len(self.groups) > 1:
            self._label_row("Page subtotal:", self._page_subtotal(self.groups[-1]))
        for i, (label, amount) in enumerate(self.doc.totals):
            last = i == len(self.doc.totals) - 1
            self._label_row(label, amount, font=BOLD if last else FONT)
        if self.case.header.notes:
            pen.need(2 * LEADING)
            pen.y -= LEADING + 6
            pen.text(MARGIN, pen.y, f"{self.doc.notes_label}:", font=BOLD)
            pen.paragraph(MARGIN, self.case.header.notes, pen.right - MARGIN)
        for block in self._blocks("below_table"):
            pen.y -= 8
            if block.title:
                pen.need(LEADING)
                pen.y -= LEADING
                pen.text(MARGIN, pen.y, block.title, font=BOLD)
            for line in block.lines:
                pen.paragraph(MARGIN, line, pen.right - MARGIN, size=block.size)
        bottom = self._blocks("bottom")
        if bottom:
            lines = [
                (wrapped, b.size)
                for b in bottom
                for line in b.lines
                for wrapped in pen.wrap(line, pen.right - MARGIN, size=b.size)
            ]
            height = sum(size + 2 for _, size in lines)
            if pen.y - height < BOTTOM:
                raise ValueError("page overflow: the bottom block doesn't fit")
            y = BOTTOM + height
            for text, size in lines:
                y -= size + 2
                pen.text(MARGIN, y, text, size=size)


def render(case: Case, doc: PdfDoc) -> PdfResult:
    return _Renderer(case, doc).run()

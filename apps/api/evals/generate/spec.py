"""The structured spec of one eval case: what the document says, and how it is printed.

Every value has a *printed* form (what the renderer writes) and a *true* form (what goes
into `expected.json`). The renderers only print; `expected.py` only reads the true forms.
Conventions for the truth (also in `evals/README.md`):
- a line's `raw_text` is its printed cells joined with one space (PDF, scan, email), or
  the cells joined with " | " (spreadsheets, as the PR 5 pipeline reads them);
- a comment line's text is its `description`; a charge line (freight) sits in the item
  table; rows of the totals block (subtotal, tax, total) are not lines;
- `notes` is only text printed under a "Notes" / "Special instructions" label;
- address parts: `attention` without its "Attn:" label; `country` only when printed.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from app.extraction.inputs.errors import InputWarningCode
from app.extraction.values import to_decimal

InputType = Literal["pdf", "png", "jpg", "xlsx", "csv", "eml"]
Group = Literal[
    "clean_pdf",
    "multipage",
    "scan",
    "sheet",
    "eml",
    "missing",
    "traps",
    "dates",
    "injection",
    "not_a_po",
]
GROUPS: tuple[Group, ...] = (
    "clean_pdf",
    "multipage",
    "scan",
    "sheet",
    "eml",
    "missing",
    "traps",
    "dates",
    "injection",
    "not_a_po",
)
# The plan's minimum number of cases per group (PR 6 plan, owner decision Q1).
GROUP_MINIMUMS: dict[Group, int] = {
    "clean_pdf": 8,
    "multipage": 4,
    "scan": 7,
    "sheet": 10,
    "eml": 4,
    "missing": 4,
    "traps": 3,
    "dates": 1,
    "injection": 4,
    "not_a_po": 1,
}
LineType = Literal["product", "charge", "comment"]
# The extra keys are columns that map to no schema field (cost center, need-by date...):
# printed from `Line.cells` only, and part of raw_text.
ColKey = Literal["no", "cpn", "vpn", "desc", "qty", "uom", "price", "ext", "x1", "x2", "x3"]


@dataclass(frozen=True, slots=True)
class Num:
    """A printed number and its true value (None: printed, but not a number, e.g. "N/C")."""

    printed: str
    value: Decimal | None


def n(printed: str) -> Num:
    """A plainly printed US number ("12", "$1,234.50", "(5.00)"): the value is read from it."""
    value = to_decimal(printed)
    if value is None:
        raise ValueError(f"not a plain number, give its value: {printed!r}")
    return Num(printed, value)


def n_as(printed: str, value: str | None) -> Num:
    """A number whose printed form needs reading by hand ("12 CS", "1.234,50", "N/C")."""
    return Num(printed, None if value is None else Decimal(value))


def money(value: Decimal) -> str:
    """Two decimals, thousands separators only with decimals (never "1,234": Q-PR5-7)."""
    return f"{value:,.2f}"


@dataclass(frozen=True, slots=True)
class DateText:
    printed: str
    iso: date | None


@dataclass(frozen=True, slots=True)
class AddressBlock:
    lines: tuple[str, ...]  # as printed, one per line
    name: str | None = None
    attention: str | None = None
    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None

    @property
    def raw_text(self) -> str:
        return "\n".join(self.lines)


@dataclass(frozen=True, slots=True)
class Header:
    customer_name: str | None = None
    po_number: str | None = None
    po_date: DateText | None = None
    ship_date: DateText | None = None
    ship_to: AddressBlock | None = None
    bill_to: AddressBlock | None = None
    order_total: Num | None = None
    currency: str | None = None  # as printed; the truth is upper-cased
    payment_terms: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class Line:
    kind: LineType = "product"
    no: str | None = None  # printed line number; true line_no only if all digits
    cpn: str | None = None  # customer part number
    vpn: str | None = None  # vendor (distributor) part number
    desc: str | None = None
    qty: Num | None = None
    uom: str | None = None
    price: Num | None = None
    ext: Num | None = None
    # Printed cell text that differs from the default (e.g. qty printed "12 CS").
    cells: tuple[tuple[ColKey, str], ...] = ()
    raw: str | None = None  # raw_text, when the renderer doesn't build it from columns

    @property
    def line_no(self) -> int | None:
        return int(self.no) if self.no is not None and self.no.isdigit() else None

    def cell(self, key: ColKey) -> str | None:
        for name, text in self.cells:
            if name == key:
                return text
        value: str | Num | None = {
            "no": self.no,
            "cpn": self.cpn,
            "vpn": self.vpn,
            "desc": self.desc,
            "qty": self.qty,
            "uom": self.uom,
            "price": self.price,
            "ext": self.ext,
        }.get(key)
        return value.printed if isinstance(value, Num) else value


@dataclass(frozen=True, slots=True)
class Col:
    key: ColKey
    heading: str
    width: float  # points
    align: Literal["l", "r"] = "l"


@dataclass(frozen=True, slots=True)
class Block:
    """Free text printed on the page (letterhead lines, fine print, injected text)."""

    lines: tuple[str, ...]
    where: Literal["top", "below_table", "bottom", "terms_page"] = "below_table"
    size: float = 9
    title: str | None = None


PdfLayout = Literal["grid", "nogrid", "two_column", "landscape", "letterhead"]


@dataclass(frozen=True, slots=True)
class PdfDoc:
    layout: PdfLayout
    columns: tuple[Col, ...]
    title: str = "PURCHASE ORDER"
    # Line indices where a new page starts (truth by construction: `page` per line).
    page_breaks: tuple[int, ...] = ()
    repeat_header: bool = True  # the table heading repeated on every page
    continued: bool = False  # "Continued on page N" at the foot of each full page
    page_subtotals: bool = False  # "Page subtotal" under each page's lines (not a line)
    totals: tuple[tuple[str, str], ...] = ()  # the totals block: (label, printed amount)
    fields: tuple[tuple[str, str], ...] = ()  # extra labelled header fields
    po_label: str = "PO Number"
    date_label: str = "PO Date"
    ship_date_label: str = "Requested Ship Date"
    terms_label: str = "Terms"
    notes_label: str = "Notes"
    blocks: tuple[Block, ...] = ()
    show_vendor: bool = True
    issuer: str | None = None  # the name on the letterhead; default: header.customer_name
    letterhead: tuple[str, ...] = ()  # lines under the issuer's name (street, city, phone)


@dataclass(frozen=True, slots=True)
class ScanDoc:
    """A PDF rendered by `render_pdf`, rasterized and degraded (`render_scan`)."""

    pdf: PdfDoc
    output: Literal["pdf", "png", "jpg"]
    dpi: int = 150
    rotate: float = 0.0  # degrees
    noise: float = 0.0  # standard deviation of the grain, 0-255 scale
    contrast: float = 1.0  # < 1: washed out
    blur: float = 0.0
    bilevel: bool = False  # fax: 1-bit, low vertical resolution
    photo: bool = False  # phone photo: uneven light, a desk border
    jpeg_quality: int = 60
    stamp: str | None = None  # a red rubber stamp across the page


CellValue = str | int | Decimal | date


@dataclass(frozen=True, slots=True)
class OracleRef:
    field: str
    row: int
    column: str
    value: str


@dataclass(frozen=True, slots=True)
class Oracle:
    """The correct `column_mapping.v1` answer for a sheet case, as far as PR 5 can take it."""

    first_data_row: int | None
    columns: tuple[tuple[str, str], ...]  # (column letter, line field)
    header_values: tuple[OracleRef, ...] = ()
    addresses: tuple[tuple[str, tuple[tuple[int, str], ...]], ...] = ()
    footer_rows: tuple[int, ...] = ()
    document_type: Literal["purchase_order", "not_a_po"] = "purchase_order"


@dataclass(frozen=True, slots=True)
class SheetDoc:
    kind: Literal["xlsx", "csv"]
    rows: tuple[tuple[CellValue | None, ...], ...]  # row 1 first; all-None = blank row
    oracle: Oracle
    merges: tuple[str, ...] = ()  # XLSX ranges, e.g. "A1:F1"
    formats: tuple[tuple[str, str], ...] = ()  # XLSX (cell ref, number format)
    widths: tuple[tuple[str, float], ...] = ()  # XLSX (column letter, width)
    sheet_title: str = "Order"
    delimiter: str = ","  # CSV
    encoding: str = "utf-8"  # CSV
    newline: str = "\r\n"  # CSV


@dataclass(frozen=True, slots=True)
class EmailDoc:
    subject: str
    from_name: str
    to: str
    plain: str | None  # the text/plain body, or None
    html: str | None = None  # a text/html body (alternative, or the only body)
    message_no: int = 1  # makes the Message-ID unique
    in_reply_to: int | None = None


Document = PdfDoc | ScanDoc | SheetDoc | EmailDoc


@dataclass(frozen=True, slots=True)
class WarningSpec:
    """An extraction warning the pipeline should give (`meta.expected_extraction_warnings`)."""

    code: str
    path: str
    rows: tuple[int, ...] = ()
    texts: tuple[str, ...] = ()
    names: tuple[str, ...] = ()
    hidden_rows: int | None = None
    hidden_columns: int | None = None
    count: int | None = None
    has_details: bool = False


@dataclass(frozen=True, slots=True)
class Case:
    name: str
    group: Group
    seed: int
    header: Header
    lines: tuple[Line, ...]
    document: Document
    sender_email: str
    received_at: datetime
    tags: tuple[str, ...] = ()
    document_type: Literal["purchase_order", "not_a_po"] = "purchase_order"
    not_scored: tuple[str, ...] = ()
    known_gap: tuple[str, ...] = ()
    expected_input_warnings: tuple[InputWarningCode, ...] = ()
    expected_extraction_warnings: tuple[WarningSpec, ...] = ()
    # Free notes for the README case table (what the case checks).
    checks: str = ""

    @property
    def input_type(self) -> InputType:
        document = self.document
        if isinstance(document, PdfDoc):
            return "pdf"
        if isinstance(document, ScanDoc):
            return document.output
        if isinstance(document, SheetDoc):
            return document.kind
        return "eml"

    @property
    def source_name(self) -> str:
        return f"source.{self.input_type}"

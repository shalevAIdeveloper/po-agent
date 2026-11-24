"""Clean text PDFs: five layouts (8 cases) and multi-page orders (4 cases)."""

import random
from collections.abc import Sequence
from decimal import Decimal

from evals.generate.cases.common import (
    CPN,
    EXT,
    NO,
    PRICE,
    QTY,
    STANDARD,
    UOM,
    d,
    letterhead,
    office,
    products,
    site,
    total,
    total_num,
    when,
)
from evals.generate.data import (
    BRINDLEWOOD,
    CORVANTHE,
    FENWHISTLE,
    HARROWMOSS,
    JUNIPERDELL,
    KESTRELWICK,
    OAKSPURROW,
    QUILLENFELD,
    SILVERQUILL,
    TALLOWFEN,
    Party,
)
from evals.generate.spec import (
    Block,
    Case,
    Col,
    DateText,
    Group,
    Header,
    Line,
    PdfDoc,
    PdfLayout,
    money,
    n,
)


def totals(lines: Sequence[Line], label: str = "Total:") -> tuple[tuple[str, str], ...]:
    return ((label, money(total(lines))),)


def header(
    party: Party,
    po: str,
    po_date: DateText | None,
    lines: Sequence[Line],
    **changes: object,
) -> Header:
    fields: dict[str, object] = {
        "customer_name": party.name,
        "po_number": po,
        "po_date": po_date,
        "ship_to": office(party),
        "bill_to": office(party),
        "order_total": total_num(list(lines)),
        "payment_terms": "Net 30",
    }
    fields.update(changes)
    return Header(**fields)  # type: ignore[arg-type]


def pdf_case(
    name: str,
    seed: int,
    party: Party,
    lines: Sequence[Line],
    head: Header,
    layout: PdfLayout,
    *,
    group: Group = "clean_pdf",
    columns: tuple[Col, ...] = STANDARD,
    day: int = 3,
    tags: tuple[str, ...] = (),
    checks: str = "",
    **doc: object,
) -> Case:
    options: dict[str, object] = {
        "letterhead": letterhead(party),
        "totals": totals(lines),
        "fields": (("Buyer", party.buyer),),
    }
    options.update(doc)
    return Case(
        name=name,
        group=group,
        seed=seed,
        header=head,
        lines=tuple(lines),
        document=PdfDoc(layout, columns, **options),  # type: ignore[arg-type]
        sender_email=party.buyer_email,
        received_at=when(day),
        tags=tags,
        checks=checks,
    )


def _grid_basic() -> Case:
    seed = 601
    rng = random.Random(seed)
    lines = products(rng, BRINDLEWOOD, 6)
    head = header(
        BRINDLEWOOD,
        "4500012345",
        d("11/02/2026", 2026, 11, 2),
        lines,
        ship_date=d("11/16/2026", 2026, 11, 16),
        ship_to=site(BRINDLEWOOD, 0),
    )
    return pdf_case(
        "pdf_grid_basic",
        seed,
        BRINDLEWOOD,
        lines,
        head,
        "grid",
        day=2,
        checks="every header field and six product lines in a ruled table",
    )


def _grid_vendor_parts() -> Case:
    seed = 602
    rng = random.Random(seed)
    lines = products(rng, HARROWMOSS, 5, cpn=False)
    columns = (
        NO,
        Col("vpn", "Vellumgrove Part #", 110),
        Col("desc", "Description", 200),
        QTY,
        UOM,
        PRICE,
        EXT,
    )
    head = header(
        HARROWMOSS, "HCM-0419", d("Nov 3, 2026", 2026, 11, 3), lines, payment_terms="2% 10 Net 30"
    )
    return pdf_case(
        "pdf_grid_vendor_parts",
        seed,
        HARROWMOSS,
        lines,
        head,
        "grid",
        columns=columns,
        checks="only the distributor's part numbers, labelled as such: vendor_part_number set, "
        "customer_part_number null",
    )


def _nogrid_aligned() -> Case:
    seed = 603
    rng = random.Random(seed)
    lines = products(rng, QUILLENFELD, 8, vpn=False)
    columns = (NO, Col("cpn", "Item", 84), Col("desc", "Description", 236), QTY, UOM, PRICE, EXT)
    head = header(
        QUILLENFELD,
        "77310",
        d("2026-11-04", 2026, 11, 4),
        lines,
        ship_to=office(QUILLENFELD, attention=QUILLENFELD.buyer),
    )
    return pdf_case(
        "pdf_nogrid_aligned",
        seed,
        QUILLENFELD,
        lines,
        head,
        "nogrid",
        columns=columns,
        day=4,
        checks="no ruling, columns aligned by position; a short numeric PO number",
    )


def _nogrid_comment_lines() -> Case:
    seed = 604
    rng = random.Random(seed)
    goods = products(rng, TALLOWFEN, 5)
    lines = [
        goods[0],
        goods[1],
        Line(kind="comment", desc="Deliver to the kitchen entrance on the east side."),
        goods[2],
        goods[3],
        Line(kind="comment", desc="Substitutes allowed for line 4 with buyer approval."),
        goods[4],
        Line(kind="charge", no="6", desc="Freight - liftgate delivery", ext=n("65.00")),
    ]
    head = header(
        TALLOWFEN, "TH-2026-1187", d("11/05/2026", 2026, 11, 5), lines, ship_to=site(TALLOWFEN, 1)
    )
    return pdf_case(
        "pdf_nogrid_comment_lines",
        seed,
        TALLOWFEN,
        lines,
        head,
        "nogrid",
        day=5,
        checks="comment lines between products (line_type comment, no line number) and a "
        "freight charge line",
    )


def _two_column_header() -> Case:
    seed = 605
    rng = random.Random(seed)
    lines = products(rng, KESTRELWICK, 4)
    head = header(
        KESTRELWICK,
        "KMW-55821",
        d("11/06/26", 2026, 11, 6),
        lines,
        ship_date=d("11/20/26", 2026, 11, 20),
        ship_to=site(KESTRELWICK, 2),
        bill_to=office(KESTRELWICK, attention="Accounts Payable", country="USA"),
        payment_terms="Net 45",
        currency="USD",
    )
    return pdf_case(
        "pdf_two_column_header",
        seed,
        KESTRELWICK,
        lines,
        head,
        "two_column",
        day=6,
        checks="addresses stacked on the left, order facts on the right; an attention line, a "
        "printed country and currency",
    )


def _landscape_extra_columns() -> Case:
    seed = 606
    rng = random.Random(seed)
    base = products(rng, CORVANTHE, 7)
    centers = ("CC-4410", "CC-4410", "CC-5120", "CC-5120", "CC-7031", "CC-7031", "CC-4410")
    need = ("12/01/26", "12/01/26", "12/08/26", "12/08/26", "12/15/26", "12/15/26", "12/01/26")
    lines = [
        Line(
            no=line.no,
            cpn=line.cpn,
            vpn=line.vpn,
            desc=line.desc,
            qty=line.qty,
            uom=line.uom,
            price=line.price,
            ext=line.ext,
            cells=(("x1", center), ("x2", date)),
        )
        for line, center, date in zip(base, centers, need, strict=True)
    ]
    columns = (
        Col("no", "Ln", 26),
        Col("cpn", "Our Item #", 74),
        Col("vpn", "Supplier Item", 78),
        Col("desc", "Description", 178),
        Col("x1", "Cost Center", 68),
        Col("x2", "Need By", 54),
        Col("qty", "Qty", 38, "r"),
        Col("uom", "Unit", 34),
        Col("price", "Unit Cost", 60, "r"),
        Col("ext", "Line Total", 64, "r"),
    )
    head = header(CORVANTHE, "CBM-PO-30552", d("11/07/2026", 2026, 11, 7), lines)
    return pdf_case(
        "pdf_landscape_extra_columns",
        seed,
        CORVANTHE,
        lines,
        head,
        "landscape",
        columns=columns,
        day=7,
        checks="a landscape page with columns the schema has no field for (cost center, a "
        "per-line need-by date): not a requested ship date",
    )


def _letterhead_fine_print() -> Case:
    seed = 607
    rng = random.Random(seed)
    lines = products(rng, JUNIPERDELL, 5)
    head = header(
        JUNIPERDELL,
        "JHC-118204",
        d("November 9, 2026", 2026, 11, 9),
        lines,
        ship_to=site(JUNIPERDELL, 3),
        notes="Please include the packing slip in box 1.",
    )
    blocks = (
        Block(
            ("Caring for the Ashgrove community", "www.juniperdell.example.com"),
            where="top",
            size=8,
        ),
        Block(
            (
                "This order is subject to the purchaser's standard terms. The vendor shall "
                "notify the purchaser of any backorder within two business days. Invoices "
                "must quote the purchase order number. Goods remain the vendor's risk until "
                "received and inspected. Questions: supplies@juniperdell.example.com.",
            ),
            where="bottom",
            size=6,
        ),
    )
    return pdf_case(
        "pdf_letterhead_fine_print",
        seed,
        JUNIPERDELL,
        lines,
        head,
        "letterhead",
        day=9,
        blocks=blocks,
        checks="a heavy letterhead and fine print at the foot of the page that are not order "
        "data; a labelled note",
    )


_LONG_ITEMS = (
    (
        "Wire rope assembly, galvanized steel, 3/8 in x 25 ft, with thimble eyes on both ends "
        "and a load tag",
        "EA",
        "48.60",
    ),
    (
        "Replacement seat cushion kit for transit bench model TB-40, vinyl, charcoal, includes "
        "mounting hardware",
        "KT",
        "112.75",
    ),
    (
        "Anti-slip stair tread tape, 4 in x 60 ft roll, black with yellow safety border",
        "RL",
        "39.20",
    ),
    (
        "Fluorescent-to-LED retrofit kit for 4 ft two-lamp fixtures, ballast bypass, 4000K, "
        "includes driver and wiring harness",
        "KT",
        "57.90",
    ),
)


def _grid_wrapped_descriptions() -> Case:
    lines = [
        Line(
            no=str(i),
            cpn=f"OTA-{2100 + 7 * i}",
            desc=desc,
            qty=n(str(qty)),
            uom=uom,
            price=n(price),
            ext=n(money(Decimal(price) * qty)),
        )
        for i, ((desc, uom, price), qty) in enumerate(
            zip(_LONG_ITEMS, (4, 12, 6, 20), strict=True), 1
        )
    ]
    columns = (NO, CPN, Col("desc", "Description", 242), QTY, UOM, PRICE, EXT)
    head = header(
        OAKSPURROW,
        "OTA-24-00917",
        d("11/10/2026", 2026, 11, 10),
        lines,
        ship_to=office(OAKSPURROW, attention="Fleet Maintenance"),
    )
    return pdf_case(
        "pdf_grid_wrapped_descriptions",
        608,
        OAKSPURROW,
        lines,
        head,
        "grid",
        columns=columns,
        day=10,
        checks="descriptions wrapped over two or three printed lines: one line each, the "
        "description whole",
    )


# --- multi-page ----------------------------------------------------------------------------


def _multipage_continued() -> Case:
    seed = 611
    rng = random.Random(seed)
    lines = products(rng, FENWHISTLE, 34)
    head = header(
        FENWHISTLE, "FRB-7781", d("11/11/2026", 2026, 11, 11), lines, ship_to=site(FENWHISTLE, 4)
    )
    return pdf_case(
        "pdf_multipage_continued",
        seed,
        FENWHISTLE,
        lines,
        head,
        "grid",
        group="multipage",
        day=11,
        page_breaks=(22,),
        repeat_header=False,
        continued=True,
        checks="the table continues on page 2 without its heading; page per line",
    )


def _multipage_repeated_header() -> Case:
    seed = 612
    rng = random.Random(seed)
    lines = products(rng, SILVERQUILL, 44)
    head = header(SILVERQUILL, "SGA-1032", d("11/12/2026", 2026, 11, 12), lines)
    return pdf_case(
        "pdf_multipage_repeated_header",
        seed,
        SILVERQUILL,
        lines,
        head,
        "nogrid",
        group="multipage",
        day=12,
        page_breaks=(20, 38),
        checks="three pages, the column headings repeated on each: never a line",
    )


def _multipage_page_subtotals() -> Case:
    seed = 613
    rng = random.Random(seed)
    lines = products(rng, BRINDLEWOOD, 36)
    head = header(
        BRINDLEWOOD,
        "4500012399",
        d("11/13/2026", 2026, 11, 13),
        lines,
        ship_to=site(BRINDLEWOOD, 0),
    )
    return pdf_case(
        "pdf_multipage_page_subtotals",
        seed,
        BRINDLEWOOD,
        lines,
        head,
        "grid",
        group="multipage",
        day=13,
        page_breaks=(21,),
        page_subtotals=True,
        checks="a page subtotal under each page's lines: not a line, not the order total",
    )


def _multipage_terms_page() -> Case:
    seed = 614
    rng = random.Random(seed)
    lines = products(rng, HARROWMOSS, 30)
    head = header(
        HARROWMOSS,
        "HCM-0433",
        d("11/16/2026", 2026, 11, 16),
        lines,
        payment_terms="Net 30",
        ship_date=d("12/04/2026", 2026, 12, 4),
    )
    terms = Block(
        (
            "1. Acceptance. Shipment of any goods on this order constitutes acceptance of "
            "these terms.",
            "2. Pricing. Prices on this order are firm. No charge may be added without the "
            "buyer's written consent.",
            "3. Delivery. Deliveries are accepted Monday to Friday, 7 am to 3 pm. Late "
            "deliveries may be refused.",
            "4. Invoices. Send invoices to the bill-to address quoting the order number.",
            "5. Warranty. The seller warrants that the goods are free of defects for one year.",
        ),
        where="terms_page",
        title="Terms and Conditions of Purchase",
    )
    return pdf_case(
        "pdf_multipage_30_lines_terms_page",
        seed,
        HARROWMOSS,
        lines,
        head,
        "letterhead",
        group="multipage",
        day=16,
        page_breaks=(16,),
        blocks=(terms,),
        checks="30 lines over two pages, then a page of terms and conditions with no lines",
    )


CASES: tuple[Case, ...] = (
    _grid_basic(),
    _grid_vendor_parts(),
    _nogrid_aligned(),
    _nogrid_comment_lines(),
    _two_column_header(),
    _landscape_extra_columns(),
    _letterhead_fine_print(),
    _grid_wrapped_descriptions(),
    _multipage_continued(),
    _multipage_repeated_header(),
    _multipage_page_subtotals(),
    _multipage_terms_page(),
)

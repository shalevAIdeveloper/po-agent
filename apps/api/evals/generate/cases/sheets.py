"""Spreadsheet orders (XLSX / CSV) for the PR 5 path: code reads, the model only maps.

The oracle (`spec.Oracle`) is the correct `column_mapping.v1` answer, within what PR 5
can accept (references inside the 40 + 10 row sample). Truths the sheet path can't
produce by design are listed in `not_scored`: address parts (raw_text only, Q-PR5-3) and
the displayed form of a real date cell (the parser reads it as ISO).
"""

import random
from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal

from evals.generate.cases.common import customer_pn, d, pick, site, when
from evals.generate.cases.sheet_builder import SheetBuilder, address_not_scored
from evals.generate.data import (
    BRINDLEWOOD,
    CORVANTHE,
    HARROWMOSS,
    KESTRELWICK,
    QUILLENFELD,
    TALLOWFEN,
    Item,
    Party,
)
from evals.generate.spec import (
    AddressBlock,
    Case,
    CellValue,
    DateText,
    Group,
    Header,
    Num,
    WarningSpec,
    money,
    n,
)

# What each column of an item row holds: a line field, or a function for an extra column.
Token = str | Callable[[int, Item], CellValue | None]


def item_cells(
    tokens: Sequence[Token | None],
    party: Party,
    no: int,
    item: Item,
    qty: int,
    kind: str,
) -> list[CellValue | None]:
    """One item row. XLSX gets typed cells (int / Decimal); CSV gets printed text."""
    ext = item.price * qty
    xlsx = kind == "xlsx"
    values: dict[str, CellValue] = {
        "no": no if xlsx else str(no),
        "cpn": customer_pn(party, item),
        "vpn": item.sku,
        "desc": item.description,
        "qty": qty if xlsx else str(qty),
        "uom": item.uom,
        "price": item.price if xlsx else money(item.price),
        "ext": ext if xlsx else money(ext),
    }
    cells: list[CellValue | None] = []
    for token in tokens:
        if token is None:
            cells.append(None)
        elif isinstance(token, str):
            cells.append(values[token])
        else:
            cells.append(token(no, item))
    return cells


def items(rng: random.Random, count: int, max_qty: int = 48) -> list[tuple[Item, int]]:
    return [(item, rng.randint(1, max_qty)) for item in pick(rng, count)]


def total_of(chosen: Sequence[tuple[Item, int]]) -> Decimal:
    return sum((item.price * qty for item, qty in chosen), Decimal(0))


def sheet_case(
    name: str,
    seed: int,
    party: Party,
    builder: SheetBuilder,
    header: Header,
    *,
    day: int,
    group: Group = "sheet",
    not_scored: tuple[str, ...] = (),
    known_gap: tuple[str, ...] = (),
    checks: str = "",
    tags: tuple[str, ...] = (),
    extra_warnings: tuple[WarningSpec, ...] = (),
    **doc: object,
) -> Case:
    for address in ("ship_to", "bill_to"):
        if getattr(header, address) is not None:
            not_scored = address_not_scored(address) + not_scored
    return Case(
        name=name,
        group=group,
        seed=seed,
        header=header,
        lines=tuple(builder.lines),
        document=builder.document(**doc),
        sender_email=party.buyer_email,
        received_at=when(day, 15, 20),
        tags=("spreadsheet", *tags),
        not_scored=not_scored,
        known_gap=known_gap,
        expected_extraction_warnings=builder.warnings() + extra_warnings,
        checks=checks,
    )


STANDARD_HEADINGS = ("Line", "Item #", "Description", "Qty", "UoM", "Unit Price", "Ext Price")
STANDARD_FIELDS = (
    "line_no",
    "customer_part_number",
    "description",
    "qty",
    "uom",
    "unit_price",
    "extended_price",
)
STANDARD_TOKENS: tuple[Token, ...] = ("no", "cpn", "desc", "qty", "uom", "price", "ext")


def _header_block_row9() -> Case:
    p, b = BRINDLEWOOD, SheetBuilder("xlsx")
    chosen = items(random.Random(901), 8)
    b.add([p.name])
    b.ref("customer_name", 1, "A", p.name)
    b.add(["Purchase Order"])
    b.add(["PO Number", "4500012402"])
    b.ref("customer_po_number", 3, "B", "4500012402")
    b.add(["PO Date", date(2026, 11, 12)])
    b.formats.append(("B4", "m/d/yyyy"))
    b.ref("po_date", 4, "B", "2026-11-12")
    ship = site(p, 0)
    b.add(["Ship To", ship.lines[0]])
    for text in ship.lines[1:]:
        b.add([None, text])
    b.address("ship_to", [(5, "B"), (6, "B"), (7, "B"), (8, "B")])
    b.heading(STANDARD_HEADINGS, STANDARD_FIELDS)
    for no, (item, qty) in enumerate(chosen, 1):
        b.line(item_cells(STANDARD_TOKENS, p, no, item, qty, "xlsx"))
    b.blank()
    total = total_of(chosen)
    footer = b.add([None, None, None, None, None, "Total", total], "footer")
    b.ref("order_total", footer, "G", b.text(footer, "G"))
    header = Header(
        customer_name=p.name,
        po_number="4500012402",
        po_date=DateText("11/12/2026", date(2026, 11, 12)),
        ship_to=ship,
        order_total=Num(b.text(footer, "G"), total),
    )
    return sheet_case(
        "xlsx_header_block_row9",
        901,
        p,
        b,
        header,
        day=12,
        not_scored=("header.po_date_raw",),
        checks="a title and header block (a real date cell, a four-cell ship-to) above "
        "headings in row 9; a footer total",
    )


def _odd_column_names() -> Case:
    p, b = HARROWMOSS, SheetBuilder("xlsx")
    chosen = items(random.Random(902), 7)
    b.add(["PO HCM-0488"])
    b.ref("customer_po_number", 1, "A", "HCM-0488")
    b.heading(
        ("Seq", "Our Ref", "Your Part #", "What", "How Many", "Per", "Each $", "Line $"),
        (
            "line_no",
            "customer_part_number",
            "vendor_part_number",
            "description",
            "qty",
            "uom",
            "unit_price",
            "extended_price",
        ),
    )
    tokens: tuple[Token, ...] = ("no", "cpn", "vpn", "desc", "qty", "uom", "price", "ext")
    for no, (item, qty) in enumerate(chosen, 1):
        b.line(item_cells(tokens, p, no, item, qty, "xlsx"))
    header = Header(po_number="HCM-0488")
    return sheet_case(
        "xlsx_odd_column_names",
        902,
        p,
        b,
        header,
        day=13,
        checks="headings in the buyer's words: 'Our Ref' is the customer's part number, "
        "'Your Part #' the distributor's; no total printed",
    )


def _merged_cells() -> Case:
    p, b = QUILLENFELD, SheetBuilder("xlsx")
    chosen = items(random.Random(903), 6)
    b.add([f"{p.name} - Purchase Order"])
    b.merges.append("A1:H1")
    b.ref("customer_name", 1, "A", p.name)
    b.add(["PO #: 77460", None, None, "Date: 11/18/2026"])
    b.merges += ["A2:C2", "D2:H2"]
    b.ref("customer_po_number", 2, "A", "77460")
    b.ref("po_date", 2, "D", "11/18/2026")
    b.blank()
    b.heading(
        ("Line", "Item", "Description", None, "Qty", "UoM", "Price", "Amount"),
        (
            "line_no",
            "customer_part_number",
            "description",
            None,
            "qty",
            "uom",
            "unit_price",
            "extended_price",
        ),
    )
    b.merges.append("C4:D4")
    tokens: tuple[Token | None, ...] = ("no", "cpn", "desc", None, "qty", "uom", "price", "ext")
    for no, (item, qty) in enumerate(chosen, 1):
        row = b.line(item_cells(tokens, p, no, item, qty, "xlsx"))
        b.merges.append(f"C{row}:D{row}")
    total = total_of(chosen)
    footer = b.add(["Total", None, None, None, None, None, None, total], "footer")
    b.merges.append(f"A{footer}:G{footer}")
    b.ref("order_total", footer, "H", b.text(footer, "H"))
    header = Header(
        customer_name=p.name,
        po_number="77460",
        po_date=d("11/18/2026", 2026, 11, 18),
        order_total=Num(b.text(footer, "H"), total),
    )
    return sheet_case(
        "xlsx_merged_cells",
        903,
        p,
        b,
        header,
        day=18,
        checks="merged title, header and description cells (the value sits in the first "
        "cell of each merge)",
    )


def _extra_columns() -> Case:
    p, b = TALLOWFEN, SheetBuilder("xlsx")
    chosen = items(random.Random(904), 6)
    b.add(["PO: TH-2026-1240"])
    b.ref("customer_po_number", 1, "A", "TH-2026-1240")
    b.heading(
        (
            "Line",
            "Item",
            "Description",
            "Bin",
            "Qty",
            "UoM",
            "Price",
            "Amount",
            "Weight (lb)",
            "Requested By",
            "Notes",
        ),
        (
            "line_no",
            "customer_part_number",
            "description",
            None,
            "qty",
            "uom",
            "unit_price",
            "extended_price",
            None,
            None,
            None,
        ),
    )
    notes = {2: "Rush", 5: "Leave at kitchen door"}
    tokens: tuple[Token, ...] = (
        "no",
        "cpn",
        "desc",
        lambda no, _: f"B-{10 + no:02d}",
        "qty",
        "uom",
        "price",
        "ext",
        lambda no, item: Decimal(len(item.description) % 9 + no) + Decimal("0.5"),
        lambda no, _: p.buyer,
        lambda no, _: notes.get(no),
    )
    for no, (item, qty) in enumerate(chosen, 1):
        b.line(item_cells(tokens, p, no, item, qty, "xlsx"))
    total = total_of(chosen)
    footer = b.add([None, None, None, None, None, None, "Total", total], "footer")
    b.ref("order_total", footer, "H", b.text(footer, "H"))
    header = Header(po_number="TH-2026-1240", order_total=Num(b.text(footer, "H"), total))
    return sheet_case(
        "xlsx_extra_columns",
        904,
        p,
        b,
        header,
        day=19,
        checks="columns with no schema field (bin, weight, requested by, notes): kept only "
        "in raw_text",
    )


def _csv_comma_quoted() -> Case:
    p, b = KESTRELWICK, SheetBuilder("csv")
    b.add(["Purchase Order", "KMW-55960"])
    b.ref("customer_po_number", 1, "B", "KMW-55960")
    b.add(["Customer", p.name])
    b.ref("customer_name", 2, "B", p.name)
    b.blank()
    b.heading(STANDARD_HEADINGS, STANDARD_FIELDS)
    rows = [
        ("KMW-10422", "Hex bolt, zinc, 3/8-16 x 2 in", "10", "BX", "18.40"),
        ("KMW-77001", 'Rope, 1/2" nylon, "marine grade", 600 ft', "2", "RL", "212.50"),
        ("KMW-77014", "Battery charger, 12V\nwith clamps and cables", "3", "EA", "84.95"),
        ("KMW-72150", "Safety glasses, clear, anti-fog", "24", "EA", "2.95"),
        ("KMW-77020", "Deck cleaner; teak safe, 1 gal", "6", "EA", "21.30"),
    ]
    total = Decimal(0)
    for no, (cpn, desc, qty, uom, price) in enumerate(rows, 1):
        ext = Decimal(price) * int(qty)
        total += ext
        b.line([str(no), cpn, desc, qty, uom, price, money(ext)])
    footer = b.add([None, None, None, None, None, "Total", money(total)], "footer")
    b.ref("order_total", footer, "G", money(total))
    header = Header(customer_name=p.name, po_number="KMW-55960", order_total=n(money(total)))
    return sheet_case(
        "csv_comma_quoted_fields",
        905,
        p,
        b,
        header,
        day=20,
        checks="comma CSV with quoted fields holding commas, double quotes, a semicolon "
        "and a line break",
    )


def _csv_semicolon() -> Case:
    p, b = CORVANTHE, SheetBuilder("csv")
    chosen = items(random.Random(906), 5)
    b.add(["PO Number", "CBM-PO-30590"])
    b.ref("customer_po_number", 1, "B", "CBM-PO-30590")
    b.add(["Order Date", "11/20/2026"])
    b.ref("po_date", 2, "B", "11/20/2026")
    b.add(["Ship To", p.name, p.street, p.city_line])
    lines = b.address("ship_to", [(3, "B"), (3, "C"), (3, "D")])
    b.blank()
    b.heading(STANDARD_HEADINGS, STANDARD_FIELDS)
    for no, (item, qty) in enumerate(chosen, 1):
        b.line(item_cells(STANDARD_TOKENS, p, no, item, qty, "csv"))
    total = total_of(chosen)
    footer = b.add([None, None, None, None, None, "Total", money(total)], "footer")
    b.ref("order_total", footer, "G", money(total))
    header = Header(
        po_number="CBM-PO-30590",
        po_date=d("11/20/2026", 2026, 11, 20),
        ship_to=AddressBlock(
            lines,
            name=p.name,
            line1=p.street,
            city=p.city,
            state=p.state,
            postal_code=p.postal_code,
        ),
        order_total=n(money(total)),
    )
    return sheet_case(
        "csv_semicolon_delimiter",
        906,
        p,
        b,
        header,
        day=20,
        delimiter=";",
        checks="';' delimiter (a comma inside the city cell is data); a ship-to across "
        "three cells of one row",
    )


BASE_CASES: tuple[Case, ...] = (
    _header_block_row9(),
    _odd_column_names(),
    _merged_cells(),
    _extra_columns(),
    _csv_comma_quoted(),
    _csv_semicolon(),
)
CASES: tuple[Case, ...] = BASE_CASES

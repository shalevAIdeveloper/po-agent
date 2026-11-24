"""Builders shared by the case modules: items, lines, addresses, columns, dates.

Randomness only through the `random.Random(seed)` a case passes in: the same seed gives
the same document on every machine.
"""

import random
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final

from evals.generate.data import CATALOG, DISTRIBUTOR, WAREHOUSES, Item, Party
from evals.generate.spec import AddressBlock, Col, DateText, Line, Num, money, n

# Standard table columns (widths fit a portrait letter page: 532 pt).
NO: Final = Col("no", "Line", 30)
CPN: Final = Col("cpn", "Our Item #", 78)
VPN: Final = Col("vpn", "Vendor Item #", 72)
DESC: Final = Col("desc", "Description", 170)
QTY: Final = Col("qty", "Qty", 36, "r")
UOM: Final = Col("uom", "UoM", 36)
PRICE: Final = Col("price", "Unit Price", 56, "r")
EXT: Final = Col("ext", "Amount", 54, "r")
STANDARD: Final = (NO, CPN, VPN, DESC, QTY, UOM, PRICE, EXT)


def when(day: int, hour: int = 9, minute: int = 30, month: int = 11) -> datetime:
    """A receive time in 2026, UTC (before the generator's date, so EML dates stay valid)."""
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


def d(printed: str, year: int, month: int, day: int) -> DateText:
    return DateText(printed, date(year, month, day))


def short(party: Party) -> str:
    """Initials of the customer's name, for its own part numbers ("BFC")."""
    words = [w for w in party.name.replace("&", " ").split() if w[0].isalpha()]
    return "".join(w[0] for w in words).upper()


def customer_pn(party: Party, item: Item) -> str:
    return f"{short(party)}-{item.sku.split('-')[1]}"


def pick(rng: random.Random, count: int) -> list[Item]:
    """`count` items; no repeats until the catalog is used up."""
    out: list[Item] = []
    while len(out) < count:
        out += rng.sample(CATALOG, min(len(CATALOG), count - len(out)))
    return out


def product(
    party: Party,
    item: Item,
    qty: int,
    *,
    no: int | None,
    cpn: bool = True,
    vpn: bool = True,
    price: bool = True,
    uom: bool = True,
) -> Line:
    if not 0 < qty < 1000:
        raise ValueError("quantities stay under 1000 (never printed as 1,234)")
    unit = item.price
    return Line(
        no=None if no is None else str(no),
        cpn=customer_pn(party, item) if cpn else None,
        vpn=item.sku if vpn else None,
        desc=item.description,
        qty=n(str(qty)),
        uom=item.uom if uom else None,
        price=n(money(unit)) if price else None,
        ext=n(money(unit * qty)) if price else None,
    )


def products(
    rng: random.Random,
    party: Party,
    count: int,
    *,
    first_no: int | None = 1,
    max_qty: int = 48,
    **options: bool,
) -> list[Line]:
    return [
        product(
            party,
            item,
            rng.randint(1, max_qty),
            no=None if first_no is None else first_no + i,
            **options,
        )
        for i, item in enumerate(pick(rng, count))
    ]


def total(lines: Sequence[Line]) -> Decimal:
    return sum(
        (line.ext.value for line in lines if line.ext is not None and line.ext.value is not None),
        Decimal(0),
    )


def total_num(lines: Sequence[Line]) -> Num:
    return n(money(total(lines)))


def office(
    party: Party, *, attention: str | None = None, country: str | None = None
) -> AddressBlock:
    lines = [party.name]
    if attention:
        lines.append(f"Attn: {attention}")
    lines += [party.street, party.city_line]
    if country:
        lines.append(country)
    return AddressBlock(
        tuple(lines),
        name=party.name,
        attention=attention,
        line1=party.street,
        city=party.city,
        state=party.state,
        postal_code=party.postal_code,
        country=country,
    )


def site(party: Party, index: int) -> AddressBlock:
    """A second site of the customer, the site printed as the attention line."""
    label, street, city, state, postal = WAREHOUSES[index]
    return AddressBlock(
        (party.name, f"Attn: {label}", street, f"{city}, {state} {postal}"),
        name=party.name,
        attention=label,
        line1=street,
        city=city,
        state=state,
        postal_code=postal,
    )


def letterhead(party: Party) -> tuple[str, ...]:
    return (party.street, party.city_line, party.phone)


def vendor_lines() -> tuple[str, ...]:
    return (DISTRIBUTOR.name, DISTRIBUTOR.street, DISTRIBUTOR.city_line)

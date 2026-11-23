"""Fictional data only: companies, people, streets, items (docs/plans/m1.md §5).

Rules, checked by `tests/evals/test_fixtures.py`:
- email domains and URL hosts are `example.com` or a subdomain of it, nothing else
  (owner rule, PR 6: no `.example` / `.test` TLDs either);
- phone numbers are `555-01xx` (reserved for fiction);
- company, person, street and city names are invented and unlikely to belong to a real
  business; no real distributor, company or product brand (also not in descriptions).
- no number is printed as `1,234` (thousands separator without decimals): Q-PR5-7 is
  still open. Quantities stay under 1000; money always has two decimals.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Final


@dataclass(frozen=True, slots=True)
class Party:
    name: str
    street: str
    city: str
    state: str
    postal_code: str
    phone: str
    domain: str
    buyer: str  # a person's name (fictional)
    buyer_email: str

    @property
    def city_line(self) -> str:
        return f"{self.city}, {self.state} {self.postal_code}"


DISTRIBUTOR: Final = Party(
    name="Vellumgrove Industrial Supply",
    street="2750 Copperleaf Parkway",
    city="Alder Falls",
    state="OH",
    postal_code="45990",
    phone="(614) 555-0100",
    domain="vellumgrove.example.com",
    buyer="Order Desk",
    buyer_email="orders@vellumgrove.example.com",
)

# The buyers (customers who send the purchase orders).
BRINDLEWOOD: Final = Party(
    "Brindlewood Facilities Co.",
    "4410 Larkspur Way",
    "Fernhill",
    "OH",
    "45991",
    "(614) 555-0142",
    "brindlewood.example.com",
    "Morgan Ellery",
    "m.ellery@brindlewood.example.com",
)
HARROWMOSS: Final = Party(
    "Harrowmoss Mechanical",
    "88 Tinmouth Road",
    "Quarry Bend",
    "PA",
    "15992",
    "(412) 555-0117",
    "harrowmoss.example.com",
    "Jordan Pike",
    "jpike@harrowmoss.example.com",
)
QUILLENFELD: Final = Party(
    "Quillenfeld Property Services",
    "1900 Saltmeadow Avenue",
    "Port Ellison",
    "NJ",
    "08993",
    "(609) 555-0163",
    "quillenfeld.example.com",
    "Avery Sundquist",
    "purchasing@quillenfeld.example.com",
)
TALLOWFEN: Final = Party(
    "Tallowfen Hospitality Group",
    "12 Wrenfield Court",
    "Cedar Hollow",
    "KY",
    "40994",
    "(502) 555-0128",
    "tallowfen.example.com",
    "Riley Okonkwo",
    "r.okonkwo@tallowfen.example.com",
)
KESTRELWICK: Final = Party(
    "Kestrelwick Marine Works",
    "301 Dockside Lane",
    "Gullhaven",
    "ME",
    "04995",
    "(207) 555-0151",
    "kestrelwick.example.com",
    "Casey Lindqvist",
    "casey@kestrelwick.example.com",
)
CORVANTHE: Final = Party(
    "Corvanthe Building Maintenance",
    "7735 Ironweed Boulevard",
    "Millbrook Station",
    "IN",
    "46996",
    "(317) 555-0139",
    "corvanthe.example.com",
    "Taylor Brannigan",
    "tbrannigan@corvanthe.example.com",
)
JUNIPERDELL: Final = Party(
    "Juniperdell Clinics",
    "55 Bellwether Street",
    "Ashgrove",
    "MI",
    "48997",
    "(616) 555-0186",
    "juniperdell.example.com",
    "Sam Whitlock",
    "supplies@juniperdell.example.com",
)
OAKSPURROW: Final = Party(
    "Oakspurrow Transit Authority",
    "2 Depot Square",
    "Lantern Ridge",
    "WV",
    "25998",
    "(304) 555-0174",
    "oakspurrow.example.com",
    "Drew Castellano",
    "dcastellano@oakspurrow.example.com",
)
FENWHISTLE: Final = Party(
    "Fenwhistle & Rowe Builders",
    "640 Quillon Drive",
    "Stonebridge Mills",
    "VA",
    "22999",
    "(540) 555-0192",
    "fenwhistlerowe.example.com",
    "Alex Marchetti",
    "amarchetti@fenwhistlerowe.example.com",
)
SILVERQUILL: Final = Party(
    "Silverquill Grove Apartments",
    "19 Thistledown Circle",
    "Harrow Point",
    "WI",
    "53990",
    "(414) 555-0108",
    "silverquill.example.com",
    "Jamie Holloway",
    "office@silverquill.example.com",
)

CUSTOMERS: Final = (
    BRINDLEWOOD,
    HARROWMOSS,
    QUILLENFELD,
    TALLOWFEN,
    KESTRELWICK,
    CORVANTHE,
    JUNIPERDELL,
    OAKSPURROW,
    FENWHISTLE,
    SILVERQUILL,
)

# Second sites (ship-to addresses other than the buyer's office).
WAREHOUSES: Final = (
    ("Receiving Dock B", "915 Gristmill Road", "Fernhill", "OH", "45991"),
    ("Central Stores", "3300 Foundry Row", "Quarry Bend", "PA", "15992"),
    ("Building 4 Loading Bay", "21 Harbor Spur", "Gullhaven", "ME", "04995"),
    ("Maintenance Shop", "480 Kilnworth Avenue", "Millbrook Station", "IN", "46996"),
    ("Job Site Trailer 3", "Lot 17, Cobbler Ridge Road", "Stonebridge Mills", "VA", "22999"),
)


@dataclass(frozen=True, slots=True)
class Item:
    sku: str  # the distributor's (vendor) part number
    description: str
    uom: str
    price: Decimal


# The distributor's catalog. Customer part numbers are made per customer (`customer_pn`).
CATALOG: Final = (
    Item("NP-10422", "Hex bolt, zinc, 3/8-16 x 2 in", "BX", Decimal("18.40")),
    Item("NP-10431", "Flat washer, zinc, 3/8 in", "BX", Decimal("6.25")),
    Item("NP-20117", "Nitrile gloves, large, 100 per box", "BX", Decimal("11.90")),
    Item("NP-20118", "Nitrile gloves, medium, 100 per box", "BX", Decimal("11.90")),
    Item("NP-31005", "LED troffer 2x4, 4000K", "EA", Decimal("64.75")),
    Item("NP-31090", "LED A19 lamp, 9W, 2700K", "EA", Decimal("3.15")),
    Item("NP-40210", "PVC elbow 90 deg, 1 in, Sch 40", "EA", Decimal("0.82")),
    Item("NP-40233", "PVC pipe, 1 in x 10 ft, Sch 40", "EA", Decimal("7.40")),
    Item("NP-51400", "Duct tape, silver, 2 in x 60 yd", "RL", Decimal("5.95")),
    Item("NP-51477", "Painter's tape, blue, 1.5 in x 60 yd", "RL", Decimal("6.80")),
    Item("NP-60012", "Trash liners, 40-45 gal, 1.5 mil", "CS", Decimal("38.50")),
    Item("NP-60019", "Paper towel, roll, 800 ft", "CS", Decimal("42.00")),
    Item("NP-60033", "Hand soap, foam, 1000 mL refill", "CS", Decimal("54.60")),
    Item("NP-72150", "Safety glasses, clear, anti-fog", "EA", Decimal("2.95")),
    Item("NP-72188", "Hi-vis vest, class 2, XL", "EA", Decimal("8.25")),
    Item("NP-80305", "Wire nut, yellow, 100 per bag", "BG", Decimal("4.10")),
    Item("NP-80344", "THHN wire, 12 AWG, black, 500 ft", "RL", Decimal("96.00")),
    Item("NP-90017", "Ball valve, brass, 3/4 in", "EA", Decimal("14.35")),
    Item("NP-90052", "PTFE thread seal tape, 1/2 in x 520 in", "EA", Decimal("0.95")),
    Item("NP-91120", "Air filter, MERV 8, 20x20x2", "EA", Decimal("6.70")),
    Item("NP-91125", "Air filter, MERV 8, 16x25x2", "EA", Decimal("6.45")),
    Item("NP-92210", "Mop head, cotton, 24 oz", "EA", Decimal("7.85")),
    Item("NP-92244", "Floor finish, high gloss, 5 gal", "PL", Decimal("71.20")),
    Item("NP-93101", "Extension cord, 12/3, 50 ft", "EA", Decimal("39.90")),
    Item("NP-93150", "Cable ties, 8 in, black, 100 per bag", "BG", Decimal("3.60")),
    Item("NP-94402", "Drywall screw, 1-5/8 in, 5 lb box", "BX", Decimal("22.30")),
    Item("NP-94460", "Wood shim, 8 in, 42 per bundle", "BD", Decimal("4.75")),
    Item("NP-95518", "Spray paint, flat black, 12 oz", "EA", Decimal("5.40")),
    Item("NP-96020", "Batteries, AA, 24 per pack", "PK", Decimal("13.80")),
    Item("NP-96027", "Batteries, 9V, 4 per pack", "PK", Decimal("9.60")),
)

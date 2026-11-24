"""Scanned and photographed orders (7): a text PDF rasterized and degraded, no text layer.

The truth is the same as for the clean PDF the scan was made from; `page` is the PDF
page (scanned PDFs) or 1 (a PNG / JPG image).
"""

import random
from typing import Any

from evals.generate.cases.common import d, products, site
from evals.generate.cases.pdf_clean import header, pdf_case
from evals.generate.data import (
    CORVANTHE,
    HARROWMOSS,
    JUNIPERDELL,
    KESTRELWICK,
    OAKSPURROW,
    SILVERQUILL,
    TALLOWFEN,
    Party,
)
from evals.generate.spec import Case, PdfDoc, PdfLayout, ScanDoc


def scan_case(
    name: str,
    seed: int,
    party: Party,
    count: int,
    po: str,
    layout: PdfLayout,
    scan: dict[str, object],
    *,
    day: int,
    checks: str,
    tags: tuple[str, ...] = (),
    **doc: Any,
) -> Case:
    lines = products(random.Random(seed), party, count)
    head = header(
        party, po, d(f"11/{day:02d}/2026", 2026, 11, day), lines, ship_to=site(party, seed % 5)
    )
    base = pdf_case(
        name,
        seed,
        party,
        lines,
        head,
        layout,
        group="scan",
        day=day,
        tags=("scan", *tags),
        checks=checks,
        **doc,
    )
    if not isinstance(base.document, PdfDoc):
        raise TypeError(name)
    fields = {f: getattr(base, f) for f in base.__dataclass_fields__}
    fields["document"] = ScanDoc(base.document, **scan)  # type: ignore[arg-type]
    return Case(**fields)


CASES: tuple[Case, ...] = (
    scan_case(
        "scan_pdf_clean_200dpi",
        701,
        HARROWMOSS,
        6,
        "HCM-0462",
        "grid",
        {"output": "pdf", "dpi": 200, "jpeg_quality": 70},
        day=6,
        checks="a clean 200 dpi scan in a PDF with no text layer",
    ),
    scan_case(
        "scan_pdf_two_page",
        702,
        KESTRELWICK,
        30,
        "KMW-55902",
        "grid",
        {"output": "pdf", "dpi": 150, "noise": 6, "jpeg_quality": 55},
        day=9,
        checks="a two-page scan: lines continue on page 2, page per line",
        tags=("multipage",),
        page_breaks=(21,),
    ),
    scan_case(
        "scan_pdf_skewed_noisy",
        703,
        TALLOWFEN,
        7,
        "TH-2026-1219",
        "nogrid",
        {"output": "pdf", "dpi": 150, "rotate": 1.6, "noise": 16, "blur": 0.6, "jpeg_quality": 50},
        day=12,
        checks="skewed 1.6 degrees, grain, specks and blur",
    ),
    scan_case(
        "scan_png_screenshot",
        704,
        CORVANTHE,
        5,
        "CBM-PO-30577",
        "two_column",
        {"output": "png", "dpi": 110},
        day=13,
        checks="a screenshot of the order at screen resolution (PNG, RGB)",
    ),
    scan_case(
        "scan_png_fax_bilevel",
        705,
        JUNIPERDELL,
        6,
        "JHC-118266",
        "grid",
        {"output": "png", "dpi": 200, "bilevel": True, "noise": 10},
        day=16,
        checks="a fax: 1 bit per pixel, half vertical resolution, specks",
    ),
    scan_case(
        "scan_jpg_phone_photo",
        706,
        SILVERQUILL,
        5,
        "SGA-1059",
        "nogrid",
        {
            "output": "jpg",
            "dpi": 130,
            "rotate": -2.2,
            "photo": True,
            "noise": 5,
            "jpeg_quality": 72,
        },
        day=18,
        checks="a phone photo: rotated, unevenly lit, warm, on a desk",
    ),
    scan_case(
        "scan_jpg_low_contrast_stamp",
        707,
        OAKSPURROW,
        6,
        "OTA-24-01022",
        "grid",
        {
            "output": "jpg",
            "dpi": 140,
            "contrast": 0.45,
            "blur": 0.5,
            "stamp": "RECEIVED NOV 19 2026",
            "jpeg_quality": 68,
        },
        day=19,
        checks="a washed-out copy with a red RECEIVED stamp over the table",
    ),
)

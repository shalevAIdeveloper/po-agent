"""`expected.json`: the document's truth, built from the spec and validated as schema v1.

`confidence` and `header_confidence` are 1.0 placeholders: the schema requires them, and
they are never scored (owner decision Q3). Truths a pipeline can't produce by design are
still written here, and listed in `meta.not_scored`.
"""

import json
from collections.abc import Sequence
from typing import Any

from app.extraction.schema import SCHEMA_VERSION, PurchaseOrderExtraction
from evals.generate.spec import (
    AddressBlock,
    Case,
    Line,
    Num,
    PdfDoc,
    ScanDoc,
)

PLACEHOLDER_CONFIDENCE = 1.0


def _value(num: Num | None) -> str | None:
    if num is None or num.value is None:
        return None
    return format(num.value, "f")


def _address(block: AddressBlock | None) -> dict[str, Any] | None:
    if block is None:
        return None
    return {
        "raw_text": block.raw_text,
        "name": block.name,
        "attention": block.attention,
        "line1": block.line1,
        "line2": block.line2,
        "city": block.city,
        "state": block.state,
        "postal_code": block.postal_code,
        "country": block.country,
    }


def raw_text(case: Case, line: Line) -> str:
    """The line as printed: the spec's `raw`, or the printed table cells joined by a space."""
    if line.raw is not None:
        return line.raw
    document = case.document
    pdf = document.pdf if isinstance(document, ScanDoc) else document
    if not isinstance(pdf, PdfDoc):
        raise ValueError(f"{case.name}: a line needs `raw` outside a PDF table")
    cells = (line.cell(col.key) for col in pdf.columns)
    return " ".join(cell for cell in cells if cell)


def build(case: Case, pages: Sequence[int | None]) -> PurchaseOrderExtraction:
    """`pages`: one per line (the renderer's pagination), None where unknown."""
    if len(pages) != len(case.lines):
        raise ValueError(f"{case.name}: one page per line")
    h = case.header
    header = {
        "customer_name": h.customer_name,
        "customer_po_number": h.po_number,
        "po_date_raw": h.po_date.printed if h.po_date else None,
        "po_date": h.po_date.iso.isoformat() if h.po_date and h.po_date.iso else None,
        "requested_ship_date_raw": h.ship_date.printed if h.ship_date else None,
        "requested_ship_date": (
            h.ship_date.iso.isoformat() if h.ship_date and h.ship_date.iso else None
        ),
        "ship_to": _address(h.ship_to),
        "bill_to": _address(h.bill_to),
        "order_total": _value(h.order_total),
        "currency": h.currency.upper() if h.currency else None,
        "payment_terms": h.payment_terms,
        "notes": h.notes,
    }
    lines = [
        {
            "raw_text": raw_text(case, line),
            "line_no": line.line_no,
            "line_type": line.kind,
            "customer_part_number": line.cpn,
            "vendor_part_number": line.vpn,
            "description": line.desc,
            "qty": _value(line.qty),
            "uom": line.uom,
            "unit_price": _value(line.price),
            "price_per": None,  # M1 PRICE-NOTATION P3 sets it from the spec
            "extended_price": _value(line.ext),
            "page": page,
            "confidence": PLACEHOLDER_CONFIDENCE,
        }
        for line, page in zip(case.lines, pages, strict=True)
    ]
    return PurchaseOrderExtraction.model_validate(
        {
            "schema_version": SCHEMA_VERSION,
            "document_type": case.document_type,
            "header": header,
            "header_confidence": PLACEHOLDER_CONFIDENCE,
            "lines": lines,
        }
    )


def dump(extraction: PurchaseOrderExtraction) -> bytes:
    """Canonical: schema field order (the fill order), two-space indent, LF, UTF-8."""
    data = extraction.model_dump(mode="json")
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def load(raw: bytes) -> PurchaseOrderExtraction:
    return PurchaseOrderExtraction.model_validate_json(raw)

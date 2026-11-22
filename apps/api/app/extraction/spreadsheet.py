"""Spreadsheet extraction: the table plus a checked column mapping, built into lines by code.

SPEC F2 (M1 PR 5). No `extract_po` call: the only model call is `map_columns` (plus at most
one retry), which returns references; every value below is copied from a cell or
normalized from it (`values.py`), so nothing is invented and a cell can't make the model
write a value that isn't in some cell.

- Lines: every table row from `first_data_row` on, except the footer rows the mapping
  names (totals, subtotals: Q-PR5-6). `raw_text` is the row's non-empty cells joined with
  " | ". A row with no value in any mapped column is a `comment` line, otherwise a
  `product` line. Unmapped fields are null (never guessed).
- Text fields are the cell text without surrounding whitespace. Numbers, line numbers and
  dates that don't normalize are null with an `unparsed_value` warning (Q-PR5-2); a
  negative qty, price or total is kept, with a `negative_value` warning (owner,
  2026-11-16).
- Line confidence is the mapping's confidence, capped at 0.5 when the line has an
  `unparsed_value` (Q-PR5-6). `header_confidence` is the mapping's confidence.
- Header facts are the exact substrings the mapping pointed to (checked); dates are
  parsed by code and keep their raw text; `currency` is an ISO code or null. Addresses
  are `raw_text` only (the referenced cells, one per line; Q-PR5-3). A header value must
  be whole tokens of its cell (`check_mapping`), and `ExtractionResult.sources` keeps the
  cell (row, column) behind each header fact and address line (`sec` M5).
- The result is validated against `po_extraction.v1` and grounded against the table's
  text as a self-check. Failure paths are as for `extract_po`; logs carry codes and
  counts only.
"""

import re
from decimal import Decimal
from typing import Any, Final

import structlog
from pydantic import ValidationError

from app.extraction.column_mapping import ColumnMapping, TableIndex, map_columns
from app.extraction.extract import ExtractionResult, ExtractionSettings, ValueSource
from app.extraction.grounding import ExtractionWarning, WarningDetails, check_grounding
from app.extraction.inputs.errors import InputWarningCode
from app.extraction.inputs.table import SheetTable
from app.extraction.schema import SCHEMA_VERSION, PurchaseOrderExtraction
from app.extraction.values import to_decimal, to_iso_date, to_line_no
from app.llm.client import LLMClient

log = structlog.get_logger("app.extraction")

CELL_SEPARATOR: Final = " | "
UNPARSED_CONFIDENCE_CAP: Final = 0.5
_TEXT_LINE_FIELDS: Final = frozenset(
    {"customer_part_number", "vendor_part_number", "description", "uom"}
)
_NUMBER_LINE_FIELDS: Final = frozenset({"qty", "unit_price", "extended_price"})
_TEXT_HEADER_FIELDS: Final = frozenset(
    {"customer_name", "customer_po_number", "payment_terms", "notes"}
)
_DATE_HEADER_FIELDS: Final = frozenset({"po_date", "requested_ship_date"})
_CURRENCY_RE: Final = re.compile(r"[A-Z]{3}")
_HEADER_FIELDS: Final = (
    "customer_name",
    "customer_po_number",
    "po_date_raw",
    "po_date",
    "requested_ship_date_raw",
    "requested_ship_date",
    "ship_to",
    "bill_to",
    "order_total",
    "currency",
    "payment_terms",
    "notes",
)
_LINE_FIELDS: Final = (
    "line_no",
    "customer_part_number",
    "vendor_part_number",
    "description",
    "qty",
    "uom",
    "unit_price",
    "extended_price",
)


class _Builder:
    def __init__(self) -> None:
        self.warnings: list[ExtractionWarning] = []

    def flag(self, code: Any, path: str) -> None:
        self.warnings.append(ExtractionWarning(code=code, path=path))

    def number(self, text: str, path: str) -> Decimal | None:
        value = to_decimal(text)
        if value is None:
            self.flag("unparsed_value", path)
        elif value < 0:
            self.flag("negative_value", path)
        return value


def source_text(table: SheetTable) -> str:
    """The table as text: each row's non-empty cells joined (the lines' `raw_text`)."""
    return "\n".join(
        CELL_SEPARATOR.join(cell for cell in row.cells if cell is not None) for row in table.rows
    )


def _header(mapping: ColumnMapping, index: TableIndex, builder: _Builder) -> dict[str, Any]:
    header: dict[str, Any] = dict.fromkeys(_HEADER_FIELDS)
    for fact in mapping.header_values:
        value = fact.value.strip()
        path = f"header.{fact.field}"
        if fact.field in _TEXT_HEADER_FIELDS:
            header[fact.field] = value
        elif fact.field in _DATE_HEADER_FIELDS:
            header[f"{fact.field}_raw"] = value
            header[fact.field] = to_iso_date(value)
            if header[fact.field] is None:
                builder.flag("unparsed_value", path)
        elif fact.field == "order_total":
            header["order_total"] = builder.number(value, path)
        else:  # currency: an ISO code as printed (any case), never a symbol
            if _CURRENCY_RE.fullmatch(value.upper()):
                header["currency"] = value.upper()
            else:
                builder.flag("unparsed_value", path)
    for address in mapping.addresses:
        cells = [index.cell(ref.row, ref.column) for ref in address.cells]
        header[address.field] = {
            "raw_text": "\n".join(cell for cell in cells if cell is not None),
            **dict.fromkeys(
                ("name", "attention", "line1", "line2", "city", "state", "postal_code", "country")
            ),
        }
    return header


def _lines(
    mapping: ColumnMapping, table: SheetTable, index: TableIndex, builder: _Builder
) -> list[dict[str, Any]]:
    first = mapping.first_data_row
    if mapping.document_type != "purchase_order" or first is None:
        return []
    footers = set(mapping.footer_rows)
    positions: dict[str, int] = {}
    for assignment in mapping.columns:
        position = index.column_index(assignment.column)
        if position is not None:
            positions[assignment.field] = position
    lines: list[dict[str, Any]] = []
    for row in table.rows:
        if row.number < first or row.number in footers:
            continue
        number = len(lines)
        line: dict[str, Any] = dict.fromkeys(_LINE_FIELDS)
        mapped = False
        unparsed = False
        for name, position in positions.items():
            cell = row.cells[position]
            if cell is None:
                continue
            mapped = True
            text = cell.strip()
            path = f"lines.{number}.{name}"
            before = len(builder.warnings)
            if name in _TEXT_LINE_FIELDS:
                line[name] = text
            elif name == "line_no":
                line[name] = to_line_no(text)
                if line[name] is None:
                    builder.flag("unparsed_value", path)
            else:
                line[name] = builder.number(text, path)
            unparsed = unparsed or any(
                w.code == "unparsed_value" for w in builder.warnings[before:]
            )
        line["raw_text"] = CELL_SEPARATOR.join(cell for cell in row.cells if cell is not None)
        line["line_type"] = "product" if mapped else "comment"
        line["page"] = None
        line["confidence"] = (
            min(mapping.confidence, UNPARSED_CONFIDENCE_CAP) if unparsed else mapping.confidence
        )
        lines.append(line)
    return lines


def value_sources(mapping: ColumnMapping) -> tuple[ValueSource, ...]:
    """The cell behind each header fact and each address line (`sec` M5)."""
    facts = [
        ValueSource(path=f"header.{fact.field}", row=fact.row, column=fact.column)
        for fact in mapping.header_values
    ]
    cells = [
        ValueSource(path=f"header.{address.field}", row=ref.row, column=ref.column)
        for address in mapping.addresses
        for ref in address.cells
    ]
    return (*facts, *cells)


def build_extraction(
    mapping: ColumnMapping, table: SheetTable
) -> tuple[PurchaseOrderExtraction, tuple[ExtractionWarning, ...]]:
    """Raises `ValidationError` only on a bug: every value is checked before."""
    index = TableIndex(table)
    builder = _Builder()
    header = _header(mapping, index, builder)
    lines = _lines(mapping, table, index, builder)
    extraction = PurchaseOrderExtraction.model_validate(
        {
            "schema_version": SCHEMA_VERSION,
            "document_type": mapping.document_type,
            "header": header,
            "header_confidence": mapping.confidence,
            "lines": lines,
        }
    )
    return extraction, tuple(builder.warnings)


TABLE_PATH: Final = "table"


def table_warnings(
    table: SheetTable, input_warnings: tuple[InputWarningCode, ...] = ()
) -> tuple[ExtractionWarning, ...]:
    """The parser's spreadsheet warnings, with their details (owner, plan item (a)).

    Sheet names are document content: they are shown to the reviewer from here, never
    logged and never sent to the model (the mapping sample never holds them).
    """
    warnings: list[ExtractionWarning] = []
    if table.other_sheets or "other_sheets_ignored" in input_warnings:
        warnings.append(
            ExtractionWarning(
                code="other_sheets_ignored",
                path=TABLE_PATH,
                details=WarningDetails(names=table.other_sheets),
            )
        )
    if table.hidden_rows or table.hidden_columns:
        warnings.append(
            ExtractionWarning(
                code="hidden_cells_ignored",
                path=TABLE_PATH,
                details=WarningDetails(
                    hidden_rows=table.hidden_rows, hidden_columns=table.hidden_columns
                ),
            )
        )
    if table.formulas_without_value:
        warnings.append(
            ExtractionWarning(
                code="formula_without_value",
                path=TABLE_PATH,
                details=WarningDetails(count=table.formulas_without_value),
            )
        )
    return tuple(warnings)


async def extract_spreadsheet(
    client: LLMClient,
    table: SheetTable,
    settings: ExtractionSettings,
    *,
    input_warnings: tuple[InputWarningCode, ...] = (),
) -> ExtractionResult:
    """`settings`: normally `column_mapping.mapping_settings(LLM_MAPPING_MODEL)`.
    `input_warnings`: the `ParsedDocument`'s, so a sheet warning without a usable name
    still shows."""
    run = await map_columns(client, table, settings)
    outcome, prompt = run.outcome, run.prompt

    def failed(code: Any) -> ExtractionResult:
        return ExtractionResult(
            status="failed",
            extraction=None,
            error_code=code,
            warnings=(),
            calls=outcome.calls,
            prompt_version=prompt.prompt_version,
            prompt_sha256=prompt.sha256,
            grounding_checked=False,
        )

    if outcome.failure is not None or outcome.parsed is None:
        return failed(outcome.failure or "schema_validation_failed")
    try:
        extraction, line_warnings = build_extraction(outcome.parsed, table)
        warnings = table_warnings(table, input_warnings) + line_warnings
    except ValidationError as error:
        # Our own build broke the schema: a bug, never the document's fault. The billed
        # calls are kept; the log has the error count only, never a value.
        log.error("extraction.spreadsheet_build_failed", error_count=error.error_count())
        return failed("schema_validation_failed")
    grounding_checked = False
    try:
        # A self-check: every value comes from a cell, so this should never flag.
        warnings += check_grounding(extraction, source_text(table))
        grounding_checked = True
    except Exception as error:
        log.warning("extraction.grounding_failed", error_type=type(error).__name__)
    log.info(
        "extraction.spreadsheet_built",
        line_count=len(extraction.lines),
        warning_count=len(warnings),
        document_type=extraction.document_type,
    )
    return ExtractionResult(
        status="extracted",
        extraction=extraction,
        error_code=None,
        warnings=warnings,
        calls=outcome.calls,
        prompt_version=prompt.prompt_version,
        prompt_sha256=prompt.sha256,
        grounding_checked=grounding_checked,
        sources=value_sources(outcome.parsed),
    )

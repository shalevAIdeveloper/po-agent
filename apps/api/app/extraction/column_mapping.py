"""Spreadsheet column mapping: the model says where things are, never what they say.

SPEC F2 (M1 PR 5): a spreadsheet is parsed by code; the mapping model (Haiku,
`LLM_MAPPING_MODEL`) sees a sample and returns `column_mapping.v1`: an enum of fields
plus cell references, which `check_mapping` verifies against the table. Every value is
then copied (or normalized) from the referenced cells by `spreadsheet.py`, so a cell can
steer *which* cell fills a field, never *what* the field says.

The sample (user turn only; nothing in the system prompt):
- the first HEAD_ROWS and the last TAIL_ROWS non-empty rows, one JSON object per line
  (`{"row": 5, "cells": {"A": "..."}}`), empty cells left out;
- each cell cut to MAX_SAMPLE_CELL_CHARS, the whole sample to MAX_SAMPLE_CHARS (the last
  rows get at most TAIL_BUDGET of it);
- neutralized like document text, plus `<table` tags, inside `<table_ID>` tags whose ID is
  a hash of the whole table (the table can't contain its own hash);
- never the sheet name.

`check_mapping` refuses: rows or columns outside the sample, a field or a column used
twice, a header value that isn't an exact non-blank substring of its (non-empty) cell,
footer rows that aren't below `first_data_row`, and a `not_a_po` with a table mapping.
Its issues name schema fields and indices only, never a value.

The committed JSON Schema is regenerated with (from apps/api):

    uv run python -m app.extraction.column_mapping app/extraction/column_mapping.v1.schema.json
"""

import hashlib
import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.extraction.extract import ExtractionSettings, delimiter_pattern, neutralize
from app.extraction.inputs.limits import MAX_CELL_CHARS, MAX_SHEET_ROW
from app.extraction.inputs.table import SheetTable
from app.extraction.schema import Confidence, Text
from app.extraction.structured_call import CallOutcome, call_with_retry
from app.llm.client import LLMClient
from app.llm.prompts import Prompt, load_prompt
from app.llm.types import StructuredRequest, TextPart, ValidationIssue

SCHEMA_VERSION: Final = "column_mapping.v1"
PROMPT_NAME: Final = "map_columns"
PROMPT_VERSION: Final = 1
# The mapping is small: a few dozen references. Effort is not sent (see mapping_settings).
MAX_OUTPUT_TOKENS: Final = 4000

HEAD_ROWS: Final = 40
TAIL_ROWS: Final = 10
MAX_SAMPLE_CELL_CHARS: Final = 80
MAX_SAMPLE_CHARS: Final = 24_000
TAIL_BUDGET: Final = 6_000
CUT_MARK: Final = "…"
TABLE_ID_CHARS: Final = 32  # 128 bits

MAX_COLUMNS_MAPPED: Final = 16
MAX_HEADER_VALUES: Final = 16
MAX_ADDRESS_CELLS: Final = 8
MAX_FOOTER_ROWS: Final = 20

_TABLE_DELIMITERS: Final = delimiter_pattern("document", "metadata", "validation_errors", "table")

LineField = Literal[
    "line_no",
    "customer_part_number",
    "vendor_part_number",
    "description",
    "qty",
    "uom",
    "unit_price",
    "extended_price",
]
HeaderField = Literal[
    "customer_name",
    "customer_po_number",
    "po_date",
    "requested_ship_date",
    "order_total",
    "currency",
    "payment_terms",
    "notes",
]
AddressField = Literal["ship_to", "bill_to"]
ColumnLetter = Annotated[str, Field(pattern=r"^[A-Z]{1,3}$")]
RowNumber = Annotated[int, Field(ge=1, le=MAX_SHEET_ROW, strict=True)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ColumnAssignment(_Model):
    column: ColumnLetter
    field: LineField


class CellRef(_Model):
    row: RowNumber
    column: ColumnLetter


class HeaderValue(_Model):
    field: HeaderField
    row: RowNumber
    column: ColumnLetter
    value: Annotated[Text, Field(max_length=MAX_CELL_CHARS)]


class AddressCells(_Model):
    field: AddressField
    cells: Annotated[list[CellRef], Field(max_length=MAX_ADDRESS_CELLS)]


class ColumnMapping(_Model):
    schema_version: Literal["column_mapping.v1"]
    document_type: Literal["purchase_order", "not_a_po"]
    first_data_row: RowNumber | None
    columns: Annotated[list[ColumnAssignment], Field(max_length=MAX_COLUMNS_MAPPED)]
    header_values: Annotated[list[HeaderValue], Field(max_length=MAX_HEADER_VALUES)]
    addresses: Annotated[list[AddressCells], Field(max_length=2)]
    footer_rows: Annotated[list[RowNumber], Field(max_length=MAX_FOOTER_ROWS)]
    confidence: Confidence


def column_letter(number: int) -> str:
    letters = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


@dataclass(frozen=True, slots=True)
class Sample:
    text: str = field(repr=False)
    rows: frozenset[int]  # row numbers shown
    columns: frozenset[str]  # column letters with a cell shown


def _cut(text: str) -> str:
    if len(text) <= MAX_SAMPLE_CELL_CHARS:
        return text
    return text[:MAX_SAMPLE_CELL_CHARS] + CUT_MARK


def _line(table: SheetTable, index: int) -> tuple[str, set[str]]:
    row = table.rows[index]
    cells = {
        column_letter(column): _cut(cell)
        for column, cell in zip(table.columns, row.cells, strict=True)
        if cell is not None
    }
    line = json.dumps({"row": row.number, "cells": cells}, ensure_ascii=False)
    return line, set(cells)


def build_sample(table: SheetTable) -> Sample:
    count = len(table.rows)
    head_indices = range(min(HEAD_ROWS, count))
    tail_indices = range(max(HEAD_ROWS, count - TAIL_ROWS), count)
    tail: list[tuple[int, str, set[str]]] = []
    used = 0
    for index in reversed(tail_indices):  # the very last rows first: totals live there
        line, letters = _line(table, index)
        if used + len(line) + 1 > TAIL_BUDGET:
            break
        tail.insert(0, (index, line, letters))
        used += len(line) + 1
    head: list[tuple[int, str, set[str]]] = []
    for index in head_indices:
        line, letters = _line(table, index)
        if used + len(line) + 1 > MAX_SAMPLE_CHARS:
            break
        head.append((index, line, letters))
        used += len(line) + 1
    chosen = head + tail
    text = neutralize("\n".join(line for _, line, _ in chosen), _TABLE_DELIMITERS)
    return Sample(
        text=text,
        rows=frozenset(table.rows[index].number for index, _, _ in chosen),
        columns=frozenset(letter for _, _, letters in chosen for letter in letters),
    )


def table_id(table: SheetTable) -> str:
    payload = {
        "v": 1,
        "columns": list(table.columns),
        "rows": [[row.number, list(row.cells)] for row in table.rows],
    }
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()[:TABLE_ID_CHARS]


def build_request(
    prompt: Prompt, table: SheetTable, sample: Sample, settings: ExtractionSettings
) -> StructuredRequest[ColumnMapping]:
    user = prompt.render("User", table_id=table_id(table), table=sample.text)
    return StructuredRequest(
        system=prompt.system,
        content=(TextPart(user),),
        output_model=ColumnMapping,
        prompt_version=prompt.prompt_version,
        schema_version=SCHEMA_VERSION,
        model=settings.model,
        max_output_tokens=settings.max_output_tokens,
        effort=settings.effort,
    )


def mapping_settings(model: str) -> ExtractionSettings:
    """`effort` is left out: it isn't known to be supported by the mapping model (Haiku);
    PR 9's live eval can set it once checked."""
    return ExtractionSettings(model=model, max_output_tokens=MAX_OUTPUT_TOKENS, effort=None)


class TableIndex:
    """Cell lookup by sheet row number and column letter."""

    def __init__(self, table: SheetTable) -> None:
        self._rows = {row.number: row for row in table.rows}
        self._columns = {column_letter(c): i for i, c in enumerate(table.columns)}

    def cell(self, row: int, column: str) -> str | None:
        found = self._rows.get(row)
        index = self._columns.get(column)
        if found is None or index is None:
            return None
        return found.cells[index]

    def column_index(self, column: str) -> int | None:
        return self._columns.get(column)


def _issue(path: str, kind: str, message: str) -> ValidationIssue:
    return ValidationIssue(path=path, type=kind, message=message)


# Characters that join two alphanumerics into one token ("12,345.00", "A-32100", "03/04").
_CONNECTORS: Final = frozenset(".,/-")
_SIGN_BEFORE: Final = frozenset("-(")
_SIGN_AFTER: Final = frozenset("-)")


def _joined_before(cell: str, start: int) -> bool:
    if start == 0:
        return False
    before = cell[start - 1]
    if before.isalnum():
        return True
    return before in _CONNECTORS and start >= 2 and cell[start - 2].isalnum()


def _joined_after(cell: str, end: int) -> bool:
    if end == len(cell):
        return False
    after = cell[end]
    if after.isalnum():
        return True
    return after in _CONNECTORS and end + 1 < len(cell) and cell[end + 1].isalnum()


def whole_token_in(cell: str, value: str, *, signed: bool = False) -> bool:
    """`value` occurs in `cell` as whole tokens (`sec` M5): never a piece of a number or an
    id ("345.00" of "12,345.00", "45000" of "4500012345"). `signed` (a money total): the
    value can't leave out a sign printed next to it ("5.00" of "(5.00)")."""
    start = cell.find(value)
    while start >= 0:
        end = start + len(value)
        ok = not _joined_before(cell, start) and not _joined_after(cell, end)
        if ok and signed:
            ok = not (start and cell[start - 1] in _SIGN_BEFORE) and not (
                end < len(cell) and cell[end] in _SIGN_AFTER
            )
        if ok:
            return True
        start = cell.find(value, start + 1)
    return False


def _duplicates(values: Iterable[str]) -> bool:
    seen = list(values)
    return len(seen) != len(set(seen))


def check_mapping(
    mapping: ColumnMapping, table: SheetTable, sample: Sample
) -> tuple[ValidationIssue, ...]:
    index = TableIndex(table)
    issues: list[ValidationIssue] = []

    def in_sample(row: int, column: str) -> bool:
        return row in sample.rows and column in sample.columns

    has_table = mapping.first_data_row is not None or mapping.columns or mapping.footer_rows
    if mapping.document_type == "not_a_po" and has_table:
        issues.append(
            _issue(
                "document_type",
                "not_a_po_with_table",
                "not_a_po must have first_data_row null and no columns or footer_rows",
            )
        )
    if mapping.first_data_row is not None and mapping.first_data_row not in sample.rows:
        issues.append(
            _issue("first_data_row", "row_not_in_sample", "must be a row number in the sample")
        )
    if mapping.columns and mapping.first_data_row is None:
        issues.append(_issue("first_data_row", "missing", "must be set when columns are mapped"))
    for i, assignment in enumerate(mapping.columns):
        if assignment.column not in sample.columns:
            issues.append(
                _issue(f"columns.{i}.column", "column_not_in_sample", "must be a sample column")
            )
    if _duplicates(a.field for a in mapping.columns):
        issues.append(_issue("columns", "duplicate_field", "each field at most once"))
    if _duplicates(a.column for a in mapping.columns):
        issues.append(_issue("columns", "duplicate_column", "each column at most once"))
    for i, header in enumerate(mapping.header_values):
        cell = index.cell(header.row, header.column)
        if not in_sample(header.row, header.column) or cell is None:
            issues.append(
                _issue(
                    f"header_values.{i}",
                    "cell_not_in_sample",
                    "row and column must be a non-empty cell in the sample",
                )
            )
        elif not header.value.strip() or not whole_token_in(
            cell, header.value, signed=header.field == "order_total"
        ):
            issues.append(
                _issue(
                    f"header_values.{i}.value",
                    "not_in_cell",
                    "must be whole words or numbers exactly as they appear in that cell",
                )
            )
    if _duplicates(h.field for h in mapping.header_values):
        issues.append(_issue("header_values", "duplicate_field", "each field at most once"))
    for i, address in enumerate(mapping.addresses):
        if not address.cells:
            issues.append(_issue(f"addresses.{i}.cells", "empty", "must list at least one cell"))
        for j, ref in enumerate(address.cells):
            if not in_sample(ref.row, ref.column) or index.cell(ref.row, ref.column) is None:
                issues.append(
                    _issue(
                        f"addresses.{i}.cells.{j}",
                        "cell_not_in_sample",
                        "must be a non-empty cell in the sample",
                    )
                )
    if _duplicates(a.field for a in mapping.addresses):
        issues.append(_issue("addresses", "duplicate_field", "each field at most once"))
    for i, row in enumerate(mapping.footer_rows):
        if (
            row not in sample.rows
            or mapping.first_data_row is None
            or row <= mapping.first_data_row
        ):
            issues.append(
                _issue(
                    f"footer_rows.{i}",
                    "bad_footer_row",
                    "must be a sample row below first_data_row",
                )
            )
    if _duplicates(str(row) for row in mapping.footer_rows):
        issues.append(_issue("footer_rows", "duplicate_row", "each row at most once"))
    return tuple(issues)


@dataclass(frozen=True, slots=True)
class MappingRun:
    prompt: Prompt
    outcome: CallOutcome[ColumnMapping]


async def map_columns(
    client: LLMClient, table: SheetTable, settings: ExtractionSettings
) -> MappingRun:
    prompt = load_prompt(PROMPT_NAME, PROMPT_VERSION)
    sample = build_sample(table)
    request = build_request(prompt, table, sample, settings)

    def check(mapping: ColumnMapping) -> tuple[ValidationIssue, ...]:
        return check_mapping(mapping, table, sample)

    outcome = await call_with_retry(client, prompt, request, "map_columns", check)
    return MappingRun(prompt=prompt, outcome=outcome)


def json_schema() -> dict[str, Any]:
    return ColumnMapping.model_json_schema(mode="validation")


def render() -> str:
    return json.dumps(json_schema(), indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> None:
    if len(argv) != 2:
        raise SystemExit("usage: python -m app.extraction.column_mapping <output.json>")
    Path(argv[1]).write_text(render(), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main(sys.argv)

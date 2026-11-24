"""Builds a sheet case's grid, its truth lines and its oracle mapping in one pass.

Each row is added with a role:
- `title`: above the table and not order data (listed in `rows_skipped` unless every
  non-empty cell is referenced by a header or address reference);
- `heading`: the column headings directly above the first data row (never listed);
- `line`: an order line (truth line and pipeline line);
- `gap`: a row the PR 5 pipeline turns into a line but that is not one (a mid-table
  subtotal or repeated heading outside the mapping sample: Follow-up M1-SHEET-TAIL);
- `footer`: totals, tax, signature (in the oracle's `footer_rows`, always listed).

The expected extraction warnings are derived from the roles and the cells: the
`rows_skipped` rule (owner, M4) and the cell normalization of `app.extraction.values`
(Q-PR5-2), which is what decides `unparsed_value` / `negative_value` for a sheet.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final, Literal

from app.extraction.column_mapping import whole_token_in
from app.extraction.values import to_decimal, to_line_no
from evals.generate.render_sheet import letter, shown
from evals.generate.spec import (
    CellValue,
    Line,
    Num,
    Oracle,
    OracleRef,
    SheetDoc,
    WarningSpec,
)

Role = Literal["title", "heading", "line", "gap", "footer"]
SEPARATOR: Final = " | "
_ATTR: Final = {
    "line_no": "no",
    "customer_part_number": "cpn",
    "vendor_part_number": "vpn",
    "description": "desc",
    "qty": "qty",
    "uom": "uom",
    "unit_price": "price",
    "extended_price": "ext",
}
_NUMERIC: Final = ("qty", "unit_price", "extended_price")
ADDRESS_PARTS: Final = (
    "name",
    "attention",
    "line1",
    "line2",
    "city",
    "state",
    "postal_code",
    "country",
)


@dataclass
class SheetBuilder:
    kind: Literal["xlsx", "csv"]
    rows: list[tuple[CellValue | None, ...]] = field(default_factory=list)
    roles: list[Role | None] = field(default_factory=list)
    columns: tuple[tuple[str, str], ...] = ()  # (letter, field): the oracle's mapping
    refs: list[OracleRef] = field(default_factory=list)
    addresses: list[tuple[str, tuple[tuple[int, str], ...]]] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    merges: list[str] = field(default_factory=list)
    formats: list[tuple[str, str]] = field(default_factory=list)

    # --- rows ----------------------------------------------------------------------------

    def add(self, cells: Sequence[CellValue | None], role: Role = "title") -> int:
        self.rows.append(tuple(cells))
        self.roles.append(role)
        return len(self.rows)

    def blank(self) -> None:
        self.rows.append(())
        self.roles.append(None)

    def text(self, row: int, column: str) -> str:
        cells = self.rows[row - 1]
        index = _index(column)
        value = cells[index] if index < len(cells) else None
        if value is None:
            raise ValueError(f"no cell at {column}{row}")
        return shown(value, self.kind)

    def heading(self, cells: Sequence[str | None], fields: Sequence[str | None]) -> int:
        """The headings row; `fields[i]` is the line field column i holds (None: unmapped)."""
        self.columns = tuple(
            (letter(i + 1), name) for i, name in enumerate(fields) if name is not None
        )
        return self.add(cells, "heading")

    def line(self, cells: Sequence[CellValue | None], *, kind: str = "product") -> int:
        """An order line: its truth is read from the mapped cells (values, not text)."""
        number = self.add(cells, "line")
        values: dict[str, object] = {}
        for column, name in self.columns:
            index = _index(column)
            value = cells[index] if index < len(cells) else None
            if value is None:
                continue
            text = shown(value, self.kind)
            attr = _ATTR[name]
            if name in _NUMERIC:
                parsed = Decimal(value) if isinstance(value, int | Decimal) else to_decimal(text)
                values[attr] = Num(text, parsed)
            elif name == "line_no":
                values[attr] = text
            else:
                values[attr] = text.strip()
        raw = SEPARATOR.join(shown(c, self.kind) for c in cells if c is not None)
        self.lines.append(Line(kind=kind, raw=raw, **values))  # type: ignore[arg-type]
        return number

    # --- references ----------------------------------------------------------------------

    def ref(self, name: str, row: int, column: str, value: str) -> None:
        cell = self.text(row, column)
        if not whole_token_in(cell, value, signed=name == "order_total"):
            raise ValueError(f"{value!r} is not whole tokens of {column}{row}")
        self.refs.append(OracleRef(name, row, column, value))

    def address(self, name: str, cells: Sequence[tuple[int, str]]) -> tuple[str, ...]:
        """The address cells in reading order; returns their texts (the address lines)."""
        texts = tuple(self.text(row, column) for row, column in cells)
        self.addresses.append((name, tuple(cells)))
        return texts

    # --- the result ----------------------------------------------------------------------

    def _non_empty(self) -> list[tuple[int, tuple[CellValue | None, ...], Role]]:
        return [
            (i + 1, cells, role)
            for i, (cells, role) in enumerate(zip(self.rows, self.roles, strict=True))
            if role is not None and any(c is not None for c in cells)
        ]

    def _row_text(self, cells: Sequence[CellValue | None]) -> str:
        return SEPARATOR.join(shown(c, self.kind) for c in cells if c is not None)

    def first_data_row(self) -> int | None:
        return next((n for n, _, role in self._non_empty() if role == "line"), None)

    def footer_rows(self) -> tuple[int, ...]:
        return tuple(n for n, _, role in self._non_empty() if role == "footer")

    def oracle(self, document_type: str = "purchase_order") -> Oracle:
        return Oracle(
            first_data_row=self.first_data_row(),
            columns=self.columns,
            header_values=tuple(self.refs),
            addresses=tuple(self.addresses),
            footer_rows=self.footer_rows(),
            document_type=document_type,  # type: ignore[arg-type]
        )

    def warnings(self) -> tuple[WarningSpec, ...]:
        """rows_skipped (every non-empty row that is no line and isn't the heading or fully
        referenced), then each pipeline line's cell warnings, in the pipeline's order."""
        rows = self._non_empty()
        first = self.first_data_row()
        if first is None:
            return ()
        used = {(r.row, r.column) for r in self.refs}
        used |= {cell for _, cells in self.addresses for cell in cells}
        above = [(n, cells, role) for n, cells, role in rows if n < first]
        skipped: list[tuple[int, str]] = []
        for i, (number, cells, role) in enumerate(above):
            heading = role == "heading" and i == len(above) - 1
            fully_used = all(
                c is None or (number, letter(j + 1)) in used for j, c in enumerate(cells)
            )
            if not heading and not fully_used:
                skipped.append((number, self._row_text(cells)))
        skipped += [(n, self._row_text(c)) for n, c, role in rows if role == "footer"]
        found: list[WarningSpec] = []
        if skipped:
            found.append(
                WarningSpec(
                    "rows_skipped",
                    "lines",
                    rows=tuple(n for n, _ in skipped),
                    texts=tuple(t for _, t in skipped),
                    has_details=True,
                )
            )
        for header_ref in self.refs:
            if header_ref.field == "order_total":
                total = to_decimal(header_ref.value)
                if total is None:
                    found.append(WarningSpec("unparsed_value", "header.order_total"))
                elif total < 0:
                    found.append(WarningSpec("negative_value", "header.order_total"))
        index = 0
        for _, cells, role in rows:
            if role not in ("line", "gap"):
                continue
            for column, name in self.columns:
                position = _index(column)
                value = cells[position] if position < len(cells) else None
                if value is None:
                    continue
                text = shown(value, self.kind).strip()
                path = f"lines.{index}.{name}"
                if name == "line_no" and to_line_no(text) is None:
                    found.append(WarningSpec("unparsed_value", path))
                elif name in _NUMERIC:
                    amount = to_decimal(text)
                    if amount is None:
                        found.append(WarningSpec("unparsed_value", path))
                    elif amount < 0:
                        found.append(WarningSpec("negative_value", path))
            index += 1
        return tuple(found)

    def document(self, **options: object) -> SheetDoc:
        width = max((len(r) for r in self.rows), default=0)
        rows = tuple(tuple(r) + (None,) * (width - len(r)) for r in self.rows)
        return SheetDoc(
            kind=self.kind,
            rows=rows,
            oracle=self.oracle(),
            merges=tuple(self.merges),
            formats=tuple(self.formats),
            gap_rows=tuple(n for n, _, role in self._non_empty() if role == "gap"),
            **options,  # type: ignore[arg-type]
        )


def _index(column: str) -> int:
    number = 0
    for char in column:
        number = number * 26 + ord(char) - ord("A") + 1
    return number - 1


def address_not_scored(name: str) -> tuple[str, ...]:
    """A sheet address is raw_text only in PR 5 (Q-PR5-3): its parts can't be produced."""
    return tuple(f"header.{name}.{part}" for part in ADDRESS_PARTS)

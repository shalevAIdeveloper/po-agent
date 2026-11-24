"""`meta.json`: what a case is, how it was made, and what is (not) scored.

`CaseMeta` forbids unknown keys and requires `synthetic: true`: a committed fixture is
always generated, never a real document (docs/plans/m1.md "Real customer documents").
"""

import json
import re
from datetime import datetime, timedelta
from typing import Annotated, Final, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from app.extraction.grounding import WarningCode
from app.extraction.inputs.errors import InputWarningCode
from app.extraction.schema import Address, PurchaseOrderHeader, PurchaseOrderLine
from evals.generate.spec import Group, InputType

META_SCHEMA_VERSION: Final = "eval_case_meta.v1"

# Owner rule (PR 6): example.com or a subdomain of it, nothing else.
_DOMAIN_RE: Final = re.compile(r"(?:[a-z0-9-]+\.)*example\.com")
_EMAIL_RE: Final = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+)")
# A dotted path into expected.json: header.<field>[.<part>] or lines.<index or *>.<field>,
# each segment a real field of the schema (dev M1: a typo must fail, never silently
# exclude nothing).
_PATH_RE: Final = re.compile(r"header\.[a-z_]+(?:\.[a-z_0-9]+)?|lines\.(?:[0-9]+|\*)\.[a-z_]+")
_HEADER_FIELDS: Final = frozenset(PurchaseOrderHeader.model_fields)
_ADDRESS_FIELDS: Final = frozenset(Address.model_fields)
_ADDRESSES: Final = frozenset({"ship_to", "bill_to"})
_LINE_FIELDS: Final = frozenset(PurchaseOrderLine.model_fields)
_FOLLOW_UP_RE: Final = re.compile(r"M[0-9]+-[A-Z0-9-]+")


def allowed_domain(domain: str) -> bool:
    return _DOMAIN_RE.fullmatch(domain.lower()) is not None


def _fictional_email(value: str) -> str:
    match = _EMAIL_RE.fullmatch(value)
    if match is None or not allowed_domain(match.group(1)):
        raise ValueError("must be an address at example.com or a subdomain of it")
    return value


def _utc(value: datetime) -> datetime:
    if value.utcoffset() != timedelta(0):
        raise ValueError("must be a UTC time")
    return value


def _path(value: str) -> str:
    if not _PATH_RE.fullmatch(value):
        raise ValueError("must be a field path: header.<field>[.<part>] or lines.<i|*>.<field>")
    parts = value.split(".")
    if parts[0] == "header":
        known = parts[1] in _HEADER_FIELDS and (
            len(parts) == 2 or (parts[1] in _ADDRESSES and parts[2] in _ADDRESS_FIELDS)
        )
    else:
        known = parts[2] in _LINE_FIELDS
    if not known:
        raise ValueError("must name fields of po_extraction.v1 (header, address or line)")
    return value


def check_line_paths(meta: "CaseMeta", line_count: int) -> None:
    """Every `lines.<i>` in not_scored is a line of the case; `lines.*` needs lines."""
    for path in meta.not_scored:
        index = path.split(".")[1] if path.startswith("lines.") else None
        if index == "*" and line_count == 0:
            raise ValueError(f"{path}: the case has no lines")
        if index is not None and index != "*" and int(index) >= line_count:
            raise ValueError(f"{path}: the case has {line_count} line(s)")


def _follow_up(value: str) -> str:
    if not _FOLLOW_UP_RE.fullmatch(value):
        raise ValueError("must be a follow-up id from docs/progress.md, e.g. M1-SHEET-TAIL")
    return value


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WarningDetailsMeta(_Model):
    names: list[str] = []
    rows: list[int] = []
    texts: list[str] = []
    hidden_rows: int | None = None
    hidden_columns: int | None = None
    count: int | None = None


class WarningMeta(_Model):
    code: WarningCode
    path: str
    details: WarningDetailsMeta | None


Tag = Annotated[str, Field(pattern=r"^[a-z0-9_]+$")]


class CaseMeta(_Model):
    synthetic: Literal[True]
    schema_version: Literal["eval_case_meta.v1"]
    generator: Literal["evals.generate"]
    generator_version: Annotated[int, Field(ge=1)]
    seed: int
    input_type: InputType
    group: Group
    tags: list[Tag]
    pages: Annotated[int, Field(ge=1)]
    sender_email: Annotated[str, AfterValidator(_fictional_email)]
    received_at: Annotated[datetime, AfterValidator(_utc)]
    expected_input_warnings: list[InputWarningCode]
    expected_extraction_warnings: list[WarningMeta]
    not_scored: list[Annotated[str, AfterValidator(_path)]]
    known_gap: list[Annotated[str, AfterValidator(_follow_up)]]
    # The raw_text of every line the pipeline is expected to add because of a known gap
    # (dev M2). PR 7 scores and reports these lines, but leaves them out of the
    # regression gate (evals/README.md).
    known_gap_raw_texts: list[Annotated[str, Field(min_length=1)]]

    @model_validator(mode="after")
    def _gap_lines_need_a_gap(self) -> Self:
        if self.known_gap_raw_texts and not self.known_gap:
            raise ValueError("known_gap_raw_texts needs a known_gap follow-up id")
        return self


def dump(meta: CaseMeta) -> bytes:
    """Canonical: field order as declared, two-space indent, LF, UTF-8."""
    data = meta.model_dump(mode="json")
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def load(raw: bytes) -> CaseMeta:
    return CaseMeta.model_validate_json(raw)

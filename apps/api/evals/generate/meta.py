"""`meta.json`: what a case is, how it was made, and what is (not) scored.

`CaseMeta` forbids unknown keys and requires `synthetic: true`: a committed fixture is
always generated, never a real document (docs/plans/m1.md "Real customer documents").
"""

import json
import re
from datetime import datetime, timedelta
from typing import Annotated, Final, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.extraction.grounding import WarningCode
from app.extraction.inputs.errors import InputWarningCode
from evals.generate.spec import Group, InputType

META_SCHEMA_VERSION: Final = "eval_case_meta.v1"

# Owner rule (PR 6): example.com or a subdomain of it, nothing else.
_DOMAIN_RE: Final = re.compile(r"(?:[a-z0-9-]+\.)*example\.com")
_EMAIL_RE: Final = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+)")
# A dotted path into expected.json: header.<field>[.<part>] or lines.<index or *>.<field>.
_PATH_RE: Final = re.compile(r"header\.[a-z_]+(?:\.[a-z_0-9]+)?|lines\.(?:[0-9]+|\*)\.[a-z_]+")
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
    return value


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


def dump(meta: CaseMeta) -> bytes:
    """Canonical: field order as declared, two-space indent, LF, UTF-8."""
    data = meta.model_dump(mode="json")
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def load(raw: bytes) -> CaseMeta:
    return CaseMeta.model_validate_json(raw)

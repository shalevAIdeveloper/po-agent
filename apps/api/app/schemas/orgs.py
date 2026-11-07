from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

Role = Literal["owner", "admin", "reviewer"]

# Mirrors the DB constraints (organizations_slug_format); the DB stays the source of truth.
SLUG_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"

OrgName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
OrgSlug = Annotated[str, StringConstraints(min_length=3, max_length=40, pattern=SLUG_PATTERN)]


class OrganizationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: OrgName
    slug: OrgSlug


class OrganizationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: OrgName


class Organization(BaseModel):
    id: UUID
    name: str
    slug: str
    role: Role


class MeResponse(BaseModel):
    user_id: UUID
    organizations: list[Organization]

"""Organizations and memberships. Every query runs as the caller under RLS."""

from dataclasses import dataclass
from typing import cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import VerifiedToken
from app.db import run_as_user, sqlstate
from app.schemas.orgs import MeResponse, Organization, Role


class OrgServiceError(Exception):
    """Expected, client-facing failure. `code` is a stable identifier for the client."""

    status_code = 400
    code = "invalid_request"


class SlugUnavailableError(OrgServiceError):
    # Deliberately generic: do not tell the caller that another tenant owns the slug.
    status_code = 409
    code = "slug_unavailable"


class OrgLimitReachedError(OrgServiceError):
    status_code = 409
    code = "organization_limit_reached"


class InvalidOrganizationError(OrgServiceError):
    status_code = 422
    code = "invalid_organization"


class OrgNotFoundError(OrgServiceError):
    status_code = 404
    code = "organization_not_found"


_SQLSTATE_ERRORS: dict[str, type[OrgServiceError]] = {
    "23505": SlugUnavailableError,  # unique_violation (slug)
    "53400": OrgLimitReachedError,  # create_organization cap
    "23514": InvalidOrganizationError,  # check_violation (name/slug/reserved slug)
}


@dataclass(frozen=True, slots=True)
class Membership:
    org_id: UUID
    role: Role


_ORG_COLUMNS = "o.id, o.name, o.slug, m.role"
_ORG_JOIN = (
    "from public.organizations o"
    " join public.memberships m on m.org_id = o.id and m.user_id = :user_id"
)


async def list_memberships(user: VerifiedToken) -> list[Membership]:
    async def work(session: AsyncSession) -> list[Membership]:
        rows = await session.execute(
            text("select org_id, role from public.memberships where user_id = :user_id"),
            {"user_id": user.user_id},
        )
        return [Membership(org_id=row.org_id, role=cast(Role, row.role)) for row in rows]

    return await run_as_user(user.claims, work)


async def get_me(user: VerifiedToken) -> MeResponse:
    async def work(session: AsyncSession) -> MeResponse:
        rows = await session.execute(
            text(f"select {_ORG_COLUMNS} {_ORG_JOIN} order by o.name, o.id"),
            {"user_id": user.user_id},
        )
        return MeResponse(
            user_id=user.user_id,
            organizations=[Organization.model_validate(row._mapping) for row in rows],
        )

    return await run_as_user(user.claims, work)


async def create_organization(user: VerifiedToken, name: str, slug: str) -> Organization:
    async def work(session: AsyncSession) -> Organization:
        org_id = (
            await session.execute(
                text("select public.create_organization(:name, :slug)"),
                {"name": name, "slug": slug},
            )
        ).scalar_one()
        row = (
            await session.execute(
                text(f"select {_ORG_COLUMNS} {_ORG_JOIN} where o.id = :org_id"),
                {"user_id": user.user_id, "org_id": org_id},
            )
        ).one()
        return Organization.model_validate(row._mapping)

    try:
        return await run_as_user(user.claims, work)
    except DBAPIError as error:
        mapped = _SQLSTATE_ERRORS.get(sqlstate(error) or "")
        if mapped is None:
            raise
        raise mapped from None


async def get_organization(user: VerifiedToken, org_id: UUID) -> Organization:
    async def work(session: AsyncSession) -> Organization | None:
        row = (
            await session.execute(
                text(f"select {_ORG_COLUMNS} {_ORG_JOIN} where o.id = :org_id"),
                {"user_id": user.user_id, "org_id": org_id},
            )
        ).one_or_none()
        return None if row is None else Organization.model_validate(row._mapping)

    org = await run_as_user(user.claims, work)
    if org is None:
        raise OrgNotFoundError
    return org


async def rename_organization(user: VerifiedToken, org_id: UUID, name: str) -> Organization:
    async def work(session: AsyncSession) -> Organization | None:
        # RLS (organizations_update_admin) is the real check; require_role is defense in depth.
        updated = await session.execute(
            text("update public.organizations set name = :name where id = :org_id returning id"),
            {"name": name, "org_id": org_id},
        )
        if updated.one_or_none() is None:
            return None
        row = (
            await session.execute(
                text(f"select {_ORG_COLUMNS} {_ORG_JOIN} where o.id = :org_id"),
                {"user_id": user.user_id, "org_id": org_id},
            )
        ).one()
        return Organization.model_validate(row._mapping)

    try:
        org = await run_as_user(user.claims, work)
    except DBAPIError as error:
        if sqlstate(error) == "23514":
            raise InvalidOrganizationError from None
        raise
    if org is None:
        raise OrgNotFoundError
    return org

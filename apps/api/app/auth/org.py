"""Active-organization resolution for org-scoped endpoints (ADR 0003)."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status

from app.auth.deps import CurrentUser
from app.auth.jwt import VerifiedToken
from app.schemas.orgs import Role
from app.services import orgs as orgs_service
from app.services.orgs import Membership


@dataclass(frozen=True, slots=True)
class OrgContext:
    user: VerifiedToken
    org_id: UUID
    role: Role


class OrgNotFoundError(Exception):
    pass


class OrgSelectionRequiredError(Exception):
    pass


def select_membership(memberships: Sequence[Membership], requested: UUID | None) -> Membership:
    if requested is not None:
        for membership in memberships:
            if membership.org_id == requested:
                return membership
        # Same answer for "does not exist" and "not yours": never confirm another org exists.
        raise OrgNotFoundError
    if len(memberships) == 1:
        return memberships[0]
    if not memberships:
        raise OrgNotFoundError
    raise OrgSelectionRequiredError


async def get_org_context(
    user: CurrentUser,
    x_org_id: Annotated[UUID | None, Header(alias="X-Org-Id")] = None,
) -> OrgContext:
    memberships = await orgs_service.list_memberships(user)
    try:
        membership = select_membership(memberships, x_org_id)
    except OrgNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found") from None
    except OrgSelectionRequiredError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "X-Org-Id header is required for users in several orgs"
        ) from None
    return OrgContext(user=user, org_id=membership.org_id, role=membership.role)


CurrentOrg = Annotated[OrgContext, Depends(get_org_context)]


def require_role(*roles: Role) -> Callable[[OrgContext], Awaitable[OrgContext]]:
    """Dependency factory: the caller must hold one of `roles` in the active org."""
    allowed = frozenset(roles)

    async def dependency(ctx: CurrentOrg) -> OrgContext:
        if ctx.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role")
        return ctx

    return dependency

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.auth.deps import CurrentUser
from app.auth.org import CurrentOrg, OrgContext, require_role
from app.schemas.orgs import Organization, OrganizationCreate, OrganizationUpdate
from app.services import orgs as orgs_service

router = APIRouter(prefix="/orgs", tags=["organizations"])

OrgAdmin = Annotated[OrgContext, Depends(require_role("owner", "admin"))]


@router.post("", response_model=Organization, status_code=status.HTTP_201_CREATED)
async def create_organization(body: OrganizationCreate, user: CurrentUser) -> Organization:
    """Creates an organization; the caller becomes its owner."""
    return await orgs_service.create_organization(user, body.name, body.slug)


@router.get("/current", response_model=Organization)
async def get_current_organization(ctx: CurrentOrg) -> Organization:
    """The active organization (X-Org-Id, or the caller's only org)."""
    return await orgs_service.get_organization(ctx.user, ctx.org_id)


@router.patch("/current", response_model=Organization)
async def update_current_organization(body: OrganizationUpdate, ctx: OrgAdmin) -> Organization:
    """Renames the active organization. Owners and admins only."""
    return await orgs_service.rename_organization(ctx.user, ctx.org_id, body.name)

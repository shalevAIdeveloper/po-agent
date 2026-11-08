from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.auth.deps import CurrentUser
from app.auth.org import CurrentOrg, OrgContext, require_role
from app.schemas.errors import AUTH_ERRORS, ORG_ERRORS, error_responses
from app.schemas.orgs import Organization, OrganizationCreate, OrganizationUpdate
from app.services import orgs as orgs_service

router = APIRouter(prefix="/orgs", tags=["organizations"])

OrgAdmin = Annotated[OrgContext, Depends(require_role("owner", "admin"))]


@router.post(
    "",
    response_model=Organization,
    status_code=status.HTTP_201_CREATED,
    # 400 invalid_request, 409 slug_unavailable / organization_limit_reached.
    # 422 is either FastAPI's validation body or `invalid_organization` (left as generated).
    responses=error_responses(*AUTH_ERRORS, 400, 409),
)
async def create_organization(body: OrganizationCreate, user: CurrentUser) -> Organization:
    """Creates an organization; the caller becomes its owner."""
    return await orgs_service.create_organization(user, body.name, body.slug)


@router.get("/current", response_model=Organization, responses=error_responses(*ORG_ERRORS))
async def get_current_organization(ctx: CurrentOrg) -> Organization:
    """The active organization (X-Org-Id, or the caller's only org)."""
    return await orgs_service.get_organization(ctx.user, ctx.org_id)


@router.patch("/current", response_model=Organization, responses=error_responses(*ORG_ERRORS, 403))
async def update_current_organization(body: OrganizationUpdate, ctx: OrgAdmin) -> Organization:
    """Renames the active organization. Owners and admins only."""
    return await orgs_service.rename_organization(ctx.user, ctx.org_id, body.name)

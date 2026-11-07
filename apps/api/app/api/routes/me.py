from fastapi import APIRouter

from app.auth.deps import CurrentUser
from app.schemas.orgs import MeResponse
from app.services import orgs as orgs_service

router = APIRouter(tags=["me"])


@router.get("/me", response_model=MeResponse)
async def me(user: CurrentUser) -> MeResponse:
    """The caller and the organizations they belong to (RLS-scoped)."""
    return await orgs_service.get_me(user)

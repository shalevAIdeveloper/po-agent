from fastapi import APIRouter

from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness only: never touches the database or external services.

    Unauthenticated, so it reveals nothing about the deployment (no env, no version; L8).
    """
    return HealthResponse(status="ok")

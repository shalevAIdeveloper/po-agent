from fastapi import APIRouter, Request

from app import __version__
from app.config import Settings
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Liveness only: never touches the database or external services."""
    settings: Settings = request.app.state.settings
    return HealthResponse(status="ok", env=settings.app_env, version=__version__)

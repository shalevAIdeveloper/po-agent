from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.jwt import (
    InvalidTokenError,
    KeySource,
    KeysUnavailableError,
    VerifiedToken,
    verify_token,
)
from app.config import Settings

log = structlog.get_logger(__name__)

_bearer = HTTPBearer(auto_error=False)


def _unauthorized() -> HTTPException:
    # One generic message for every failure: never tell the caller why a token failed.
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> VerifiedToken:
    if credentials is None:
        raise _unauthorized()

    settings: Settings = request.app.state.settings
    keys: KeySource | None = request.app.state.jwks
    if keys is None or settings.jwt_issuer is None:
        log.error("auth.not_configured")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Authentication unavailable")

    try:
        return await verify_token(credentials.credentials, keys=keys, issuer=settings.jwt_issuer)
    except KeysUnavailableError:
        # Our problem, not the caller's: a 401 here would look like a logout to the web app.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Authentication unavailable"
        ) from None
    except InvalidTokenError as error:
        # The reason is a fixed string or an exception class name, never token content.
        log.info("auth.token_rejected", reason=str(error))
        raise _unauthorized() from None


CurrentUser = Annotated[VerifiedToken, Depends(get_current_user)]

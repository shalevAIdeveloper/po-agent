"""Supabase access-token verification against the project's JWKS (ADR 0003).

Only asymmetric algorithms are accepted: the API holds no shared JWT secret,
and an attacker cannot downgrade to HS256 (signing with the public key) or
`none`.
"""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from time import monotonic as _monotonic
from typing import Any, Protocol
from uuid import UUID

import httpx
import jwt
import structlog

ALLOWED_ALGORITHMS: frozenset[str] = frozenset({"ES256", "RS256"})
AUDIENCE = "authenticated"
LEEWAY_SECONDS = 30
JWKS_TTL_SECONDS = 600.0
# If the JWKS endpoint is down, keys older than the TTL are still trusted for this
# long, then dropped: a rotated-out (possibly revoked) key must not live forever.
JWKS_STALE_GRACE_SECONDS = 3600.0
# Refetches (unknown `kid`, or retries after a failure) happen at most this often.
JWKS_MIN_REFRESH_INTERVAL_SECONDS = 30.0
JWKS_TIMEOUT_SECONDS = 5.0

log = structlog.get_logger(__name__)


class InvalidTokenError(Exception):
    """The token must be rejected. The message is for logs, never for clients."""


class KeysUnavailableError(Exception):
    """No usable signing keys (JWKS unreachable and nothing fresh cached): a 503, not a 401."""


@dataclass(frozen=True, slots=True)
class VerifiedToken:
    user_id: UUID
    claims: Mapping[str, Any]


class KeySource(Protocol):
    async def get_key(self, kid: str) -> jwt.PyJWK | None:
        """The key for `kid`, None if unknown; raises KeysUnavailableError if none usable."""
        ...


class JwksClient:
    """Async JWKS fetcher: TTL cache, rate-limited refreshes, bounded use of stale keys."""

    def __init__(self, jwks_url: str, *, http: httpx.AsyncClient | None = None) -> None:
        self._url = jwks_url
        self._http = http
        self._keys: dict[str, jwt.PyJWK] = {}
        self._last_attempt: float | None = None
        self._last_success: float | None = None
        self._lock = asyncio.Lock()

    async def get_key(self, kid: str) -> jwt.PyJWK | None:
        # Also wait when a refresh is already in flight and we lack this kid: it may be
        # the first fetch (no keys yet → would be a 503) or a rotation bringing the kid
        # (→ would be a 401). Callers with a known kid never wait (A7 covers the rest).
        in_flight = self._lock.locked() and kid not in self._keys
        if in_flight or self._should_refresh(kid, _monotonic()):
            async with self._lock:
                # Re-check: a concurrent caller may have refreshed while we waited.
                if self._should_refresh(kid, _monotonic()):
                    await self._refresh()

        if self._last_success is None or not self._keys:
            raise KeysUnavailableError
        if _monotonic() - self._last_success > JWKS_TTL_SECONDS + JWKS_STALE_GRACE_SECONDS:
            self._keys = {}
            log.error("auth.jwks_stale")
            raise KeysUnavailableError
        return self._keys.get(kid)

    def _should_refresh(self, kid: str, now: float) -> bool:
        if self._last_attempt is None:
            return True
        if now - self._last_attempt <= JWKS_MIN_REFRESH_INTERVAL_SECONDS:
            return False
        expired = self._last_success is None or now - self._last_success > JWKS_TTL_SECONDS
        return expired or kid not in self._keys

    async def _refresh(self) -> None:
        self._last_attempt = _monotonic()
        try:
            # httpx timeouts are per phase; this bounds the whole fetch.
            async with asyncio.timeout(JWKS_TIMEOUT_SECONDS):
                if self._http is not None:
                    response = await self._http.get(self._url, timeout=JWKS_TIMEOUT_SECONDS)
                else:
                    async with httpx.AsyncClient() as client:
                        response = await client.get(self._url, timeout=JWKS_TIMEOUT_SECONDS)
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("JWKS body is not an object")
            key_set = jwt.PyJWKSet.from_dict(body)
        except (TimeoutError, httpx.HTTPError, ValueError, TypeError, jwt.PyJWTError):
            # Keep the cached keys (within the grace period); retry after the interval.
            log.warning("auth.jwks_refresh_failed")
            return
        self._keys = {key.key_id: key for key in key_set.keys if key.key_id}
        self._last_success = _monotonic()


async def verify_token(token: str, *, keys: KeySource, issuer: str) -> VerifiedToken:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as error:
        raise InvalidTokenError("malformed token") from error

    algorithm = header.get("alg")
    if algorithm not in ALLOWED_ALGORITHMS:
        raise InvalidTokenError("algorithm not allowed")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise InvalidTokenError("missing kid")

    key = await keys.get_key(kid)
    if key is None:
        raise InvalidTokenError("unknown kid")
    if key.algorithm_name != algorithm:
        raise InvalidTokenError("algorithm does not match key")

    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            key=key.key,
            algorithms=[algorithm],
            audience=AUDIENCE,
            issuer=issuer,
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except jwt.PyJWTError as error:
        raise InvalidTokenError(type(error).__name__) from error

    if claims.get("role") != "authenticated":
        raise InvalidTokenError("role is not authenticated")
    if claims.get("is_anonymous") is True:
        raise InvalidTokenError("anonymous session")
    try:
        user_id = UUID(str(claims["sub"]))
    except ValueError as error:
        raise InvalidTokenError("sub is not a uuid") from error

    return VerifiedToken(user_id=user_id, claims=claims)

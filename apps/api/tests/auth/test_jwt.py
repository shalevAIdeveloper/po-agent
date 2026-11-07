import asyncio
import time
from collections.abc import Iterator
from uuid import uuid4

import httpx
import pytest

from app.auth import jwt as jwt_module
from app.auth.jwt import (
    JWKS_MIN_REFRESH_INTERVAL_SECONDS,
    JWKS_STALE_GRACE_SECONDS,
    JWKS_TTL_SECONDS,
    InvalidTokenError,
    JwksClient,
    KeysUnavailableError,
    verify_token,
)
from tests.auth.keys import (
    ISSUER,
    StaticKeys,
    claims,
    forge_alg_none,
    forge_hs256_with_public_key,
    make_es256_key,
    make_rs256_key,
    sign,
    tamper_payload,
)

ES_KEY = make_es256_key()
RS_KEY = make_rs256_key()
KEYS = StaticKeys.of(ES_KEY, RS_KEY)


async def _verify(token: str) -> None:
    await verify_token(token, keys=KEYS, issuer=ISSUER)


async def test_valid_es256_token_is_accepted() -> None:
    user_id = uuid4()

    verified = await verify_token(sign(ES_KEY, claims(user_id)), keys=KEYS, issuer=ISSUER)

    assert verified.user_id == user_id
    assert verified.claims["role"] == "authenticated"


async def test_valid_rs256_token_is_accepted() -> None:
    verified = await verify_token(sign(RS_KEY, claims()), keys=KEYS, issuer=ISSUER)

    assert verified.claims["aud"] == "authenticated"


async def test_small_clock_skew_is_tolerated() -> None:
    now = int(time.time())

    await _verify(sign(ES_KEY, claims(exp=now - 10, iat=now - 3600)))


# Each case names the check that must reject it, so removing one check (e.g. the
# algorithm allowlist) fails its case even if a later check would still reject.
@pytest.mark.parametrize(
    ("label", "token", "reason"),
    [
        ("expired", sign(ES_KEY, claims(exp=int(time.time()) - 120)), "ExpiredSignatureError"),
        (
            "not yet valid",
            sign(ES_KEY, claims(nbf=int(time.time()) + 300)),
            "ImmatureSignatureError",
        ),
        ("wrong audience", sign(ES_KEY, claims(aud="anon")), "InvalidAudienceError"),
        (
            "wrong issuer",
            sign(ES_KEY, claims(iss="https://evil.example/auth/v1")),
            "InvalidIssuerError",
        ),
        ("missing exp", sign(ES_KEY, claims(exp=None)), "MissingRequiredClaimError"),
        ("missing iat", sign(ES_KEY, claims(iat=None)), "MissingRequiredClaimError"),
        ("missing sub", sign(ES_KEY, claims(sub=None)), "MissingRequiredClaimError"),
        ("missing aud", sign(ES_KEY, claims(aud=None)), "MissingRequiredClaimError"),
        ("missing iss", sign(ES_KEY, claims(iss=None)), "MissingRequiredClaimError"),
        ("missing role", sign(ES_KEY, claims(role=None)), "role is not authenticated"),
        ("sub is not a uuid", sign(ES_KEY, claims(sub="admin")), "sub is not a uuid"),
        (
            "role is service_role",
            sign(ES_KEY, claims(role="service_role")),
            "role is not authenticated",
        ),
        ("role is anon", sign(ES_KEY, claims(role="anon")), "role is not authenticated"),
        ("anonymous session", sign(ES_KEY, claims(is_anonymous=True)), "anonymous session"),
        (
            "HS256 signed with the public key",
            forge_hs256_with_public_key(ES_KEY, claims()),
            "algorithm not allowed",
        ),
        ("alg none", forge_alg_none(ES_KEY, claims()), "algorithm not allowed"),
        ("unknown kid", sign(make_es256_key("rogue-kid"), claims()), "unknown kid"),
        ("missing kid", sign(ES_KEY, claims(), kid=None), "missing kid"),
        (
            "tampered sub",
            tamper_payload(sign(ES_KEY, claims()), sub=str(uuid4())),
            "InvalidSignatureError",
        ),
        (
            "tampered role",
            tamper_payload(sign(ES_KEY, claims(role="anon")), role="authenticated"),
            "InvalidSignatureError",
        ),
        (
            "header alg does not match key",
            sign(RS_KEY, claims(), kid=ES_KEY.kid),
            "algorithm does not match key",
        ),
        ("unknown critical header", sign(ES_KEY, claims(), crit=["x-po"]), "malformed token"),
        ("garbage", "not-a-jwt", "malformed token"),
        ("empty", "", "malformed token"),
    ],
)
async def test_invalid_tokens_are_rejected_for_the_right_reason(
    label: str, token: str, reason: str
) -> None:
    with pytest.raises(InvalidTokenError, match=f"^{reason}$"):
        await _verify(token)


async def test_unknown_kid_is_looked_up_once() -> None:
    keys = StaticKeys.of(ES_KEY)

    with pytest.raises(InvalidTokenError, match="unknown kid"):
        await verify_token(sign(make_es256_key("other"), claims()), keys=keys, issuer=ISSUER)

    assert keys.lookups == ["other"]


async def test_disallowed_algorithm_is_rejected_before_any_key_lookup() -> None:
    keys = StaticKeys.of(ES_KEY)

    for token in (forge_alg_none(ES_KEY, claims()), forge_hs256_with_public_key(ES_KEY, claims())):
        with pytest.raises(InvalidTokenError, match="algorithm not allowed"):
            await verify_token(token, keys=keys, issuer=ISSUER)

    assert keys.lookups == []


# ----- JwksClient (fake clock) -----


class _Clock:
    def __init__(self) -> None:
        # A small value on purpose: the first fetch must not depend on the clock.
        self.now = 1.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Clock]:
    fake = _Clock()
    # Patch only this module's clock: asyncio's own clock (time.monotonic) must keep running.
    monkeypatch.setattr(jwt_module, "_monotonic", fake)
    yield fake


class _Endpoint:
    """JWKS endpoint whose key set / availability tests can change between calls."""

    def __init__(self, *kids: str) -> None:
        self.kids = list(kids)
        self.status = 200
        self.calls = 0

    def handler(self, _request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "down"})
        return httpx.Response(200, json={"keys": [make_es256_key(k).public_jwk for k in self.kids]})


@pytest.fixture
async def endpoint() -> _Endpoint:
    return _Endpoint("k1", "k2")


@pytest.fixture
async def client(endpoint: _Endpoint) -> Iterator[JwksClient]:  # type: ignore[misc]
    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint.handler)) as http:
        yield JwksClient("http://supabase.test/jwks", http=http)


@pytest.mark.usefixtures("clock")
async def test_fetches_once_and_caches(client: JwksClient, endpoint: _Endpoint) -> None:
    assert await client.get_key("k1") is not None
    assert await client.get_key("k2") is not None

    assert endpoint.calls == 1


@pytest.mark.usefixtures("clock")
async def test_unknown_kids_refetch_at_most_once_per_interval(
    client: JwksClient, endpoint: _Endpoint
) -> None:
    await client.get_key("k1")
    for index in range(5):
        assert await client.get_key(f"unknown-{index}") is None

    assert endpoint.calls == 1


async def test_ttl_expiry_refetches(client: JwksClient, endpoint: _Endpoint, clock: _Clock) -> None:
    await client.get_key("k1")
    clock.advance(JWKS_TTL_SECONDS + 1)

    await client.get_key("k1")

    assert endpoint.calls == 2


async def test_rotated_in_key_is_accepted_after_the_refresh_interval(
    client: JwksClient, endpoint: _Endpoint, clock: _Clock
) -> None:
    await client.get_key("k1")
    endpoint.kids.append("k3")

    assert await client.get_key("k3") is None  # inside the interval: no refetch yet
    clock.advance(JWKS_MIN_REFRESH_INTERVAL_SECONDS + 1)

    assert await client.get_key("k3") is not None
    assert endpoint.calls == 2


async def test_failing_endpoint_keeps_cached_keys_within_grace(
    client: JwksClient, endpoint: _Endpoint, clock: _Clock
) -> None:
    assert await client.get_key("k1") is not None
    endpoint.status = 503
    clock.advance(JWKS_TTL_SECONDS + 1)

    assert await client.get_key("k1") is not None
    assert endpoint.calls == 2


async def test_stale_keys_are_dropped_after_grace(
    client: JwksClient, endpoint: _Endpoint, clock: _Clock
) -> None:
    await client.get_key("k1")
    endpoint.status = 503
    clock.advance(JWKS_TTL_SECONDS + JWKS_STALE_GRACE_SECONDS + 1)

    with pytest.raises(KeysUnavailableError):
        await client.get_key("k1")


@pytest.mark.usefixtures("clock")
async def test_no_keys_at_all_is_unavailable_not_unknown(
    client: JwksClient, endpoint: _Endpoint
) -> None:
    endpoint.status = 503

    with pytest.raises(KeysUnavailableError):
        await client.get_key("k1")


@pytest.mark.usefixtures("clock")
async def test_concurrent_callers_share_one_fetch(client: JwksClient, endpoint: _Endpoint) -> None:
    results = await asyncio.gather(*(client.get_key("k1") for _ in range(10)))

    assert all(result is not None for result in results)
    assert endpoint.calls == 1


@pytest.mark.usefixtures("clock")
@pytest.mark.parametrize("body", [[], None, [1], "keys", 42])
async def test_non_object_jwks_body_is_unavailable_not_a_crash(body: object) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = JwksClient("http://supabase.test/jwks", http=http)

        with pytest.raises(KeysUnavailableError):
            await client.get_key("k1")


@pytest.mark.usefixtures("clock")
async def test_slow_jwks_endpoint_is_bounded_by_a_total_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jwt_module, "JWKS_TIMEOUT_SECONDS", 0.05)

    async def handler(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json={"keys": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = JwksClient("http://supabase.test/jwks", http=http)
        started = time.perf_counter()

        with pytest.raises(KeysUnavailableError):
            await client.get_key("k1")

    assert time.perf_counter() - started < 1

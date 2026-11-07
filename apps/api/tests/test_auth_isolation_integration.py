"""Two real users, real GoTrue tokens, the real API as `po_api`: org isolation end to end.

Needs a local Supabase stack (`supabase start`) and these env vars (see ci.yml `db` job):
INTEGRATION_DATABASE_URL (postgres, setup/cleanup only), INTEGRATION_API_DATABASE_URL (po_api),
INTEGRATION_SUPABASE_URL, INTEGRATION_SUPABASE_PUBLISHABLE_KEY, INTEGRATION_SUPABASE_SECRET_KEY.
"""

import json
import time
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import httpx
import jwt
import pytest
import structlog
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import db
from app.auth.jwt import ALLOWED_ALGORITHMS, JwksClient, VerifiedToken, verify_token
from app.config import Settings
from app.main import create_app
from app.services import orgs as orgs_service
from tests.integration_support import require_env

pytestmark = pytest.mark.integration

PASSWORD = "Integration-Passw0rd!"
log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Env:
    admin_db: str
    api_db: str
    supabase_url: str
    publishable_key: str
    secret_key: str

    @property
    def issuer(self) -> str:
        return f"{self.supabase_url}/auth/v1"


@dataclass(frozen=True)
class User:
    id: UUID
    email: str
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


@pytest.fixture
def env() -> Env:
    return Env(
        admin_db=require_env("INTEGRATION_DATABASE_URL"),
        api_db=require_env("INTEGRATION_API_DATABASE_URL"),
        supabase_url=require_env("INTEGRATION_SUPABASE_URL").rstrip("/"),
        publishable_key=require_env("INTEGRATION_SUPABASE_PUBLISHABLE_KEY"),
        secret_key=require_env("INTEGRATION_SUPABASE_SECRET_KEY"),
    )


async def _admin_execute(env: Env, sql: str, *args: Any) -> None:
    conn = await asyncpg.connect(env.admin_db)
    try:
        await conn.execute(sql, *args)
    finally:
        await conn.close()


async def _delete_user(gotrue: httpx.AsyncClient, env: Env, user_id: UUID) -> None:
    # Orgs first (cascades memberships): the last-owner guard blocks deleting owners.
    try:
        await _admin_execute(
            env, "delete from public.organizations where created_by = $1::uuid", user_id
        )
    finally:
        response = await gotrue.delete(
            f"/auth/v1/admin/users/{user_id}", headers={"apikey": env.secret_key}
        )
        if response.status_code not in (200, 204, 404):
            log.warning("integration.user_cleanup_failed", status=response.status_code)


@pytest.fixture
async def make_user(env: Env) -> AsyncIterator[Callable[[str], Coroutine[Any, Any, User]]]:
    """Creates real GoTrue users; each one is cleaned up even if later setup fails."""
    async with AsyncExitStack() as cleanup:
        gotrue = await cleanup.enter_async_context(
            httpx.AsyncClient(base_url=env.supabase_url, timeout=10)
        )
        suffix = uuid4().hex[:10]

        async def create(label: str) -> User:
            email = f"it-{label}-{suffix}@integration.test"
            created = await gotrue.post(
                "/auth/v1/admin/users",
                headers={"apikey": env.secret_key},
                json={"email": email, "password": PASSWORD, "email_confirm": True},
            )
            created.raise_for_status()
            user_id = UUID(created.json()["id"])
            cleanup.push_async_callback(_delete_user, gotrue, env, user_id)
            session = await gotrue.post(
                "/auth/v1/token?grant_type=password",
                headers={"apikey": env.publishable_key},
                json={"email": email, "password": PASSWORD},
            )
            session.raise_for_status()
            return User(id=user_id, email=email, token=session.json()["access_token"])

        yield create


@pytest.fixture
async def api(env: Env, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        _env_file=None, database_url=SecretStr(env.api_db), supabase_url=env.supabase_url
    )
    monkeypatch.setattr(db, "get_settings", lambda: settings)
    db.get_engine.cache_clear()
    db.get_sessionmaker.cache_clear()
    transport = httpx.ASGITransport(app=create_app(settings))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as client:
            yield client
    finally:
        await db.dispose_engine()


async def _verified(env: Env, user: User) -> VerifiedToken:
    return await verify_token(
        user.token, keys=JwksClient(f"{env.issuer}/.well-known/jwks.json"), issuer=env.issuer
    )


async def _create_org(api: httpx.AsyncClient, user: User, slug: str) -> str:
    response = await api.post(
        "/orgs", headers=user.headers, json={"name": f"Org {slug}", "slug": slug}
    )
    assert response.status_code == 201, response.text
    assert response.json()["role"] == "owner"
    return str(response.json()["id"])


async def test_real_gotrue_tokens_use_an_allowed_algorithm(
    make_user: Callable[[str], Coroutine[Any, Any, User]],
) -> None:
    user = await make_user("alg")

    assert jwt.get_unverified_header(user.token)["alg"] in ALLOWED_ALGORITHMS


async def test_two_users_are_isolated_through_the_api(
    api: httpx.AsyncClient, make_user: Callable[[str], Coroutine[Any, Any, User]]
) -> None:
    user_a, user_b = await make_user("a"), await make_user("b")
    suffix = uuid4().hex[:8]
    org_a = await _create_org(api, user_a, f"it-org-a-{suffix}")
    org_b = await _create_org(api, user_b, f"it-org-b-{suffix}")

    # Each user sees exactly their own org.
    me_a = (await api.get("/me", headers=user_a.headers)).json()
    me_b = (await api.get("/me", headers=user_b.headers)).json()
    assert [org["id"] for org in me_a["organizations"]] == [org_a]
    assert [org["id"] for org in me_b["organizations"]] == [org_b]
    assert me_a["user_id"] == str(user_a.id)

    # The only org is selected without a header.
    current = await api.get("/orgs/current", headers=user_a.headers)
    assert current.json()["id"] == org_a

    # Pointing at the other tenant's org (or a made-up one): 404, never 403, never data.
    for org_id in (org_b, str(uuid4())):
        foreign = {**user_a.headers, "X-Org-Id": org_id}
        read = await api.get("/orgs/current", headers=foreign)
        write = await api.patch("/orgs/current", headers=foreign, json={"name": "pwned"})
        assert (read.status_code, write.status_code) == (404, 404)
        assert "Org it-org-b" not in read.text + write.text

    # Org B is untouched.
    org_b_now = (await api.get("/orgs/current", headers=user_b.headers)).json()
    assert org_b_now["name"] == f"Org it-org-b-{suffix}"

    # Claiming the other tenant's slug: generic 409, no hint of who owns it.
    taken = await api.post(
        "/orgs", headers=user_a.headers, json={"name": "Squat", "slug": f"it-org-b-{suffix}"}
    )
    assert (taken.status_code, taken.json()) == (409, {"detail": "slug_unavailable"})

    # The owner can rename their own org through the API.
    renamed = await api.patch("/orgs/current", headers=user_a.headers, json={"name": "A renamed"})
    assert (renamed.status_code, renamed.json()["name"]) == (200, "A renamed")


async def test_reviewer_in_two_orgs_cannot_rename_even_past_require_role(
    api: httpx.AsyncClient, env: Env, make_user: Callable[[str], Coroutine[Any, Any, User]]
) -> None:
    owner, reviewer = await make_user("owner"), await make_user("reviewer")
    suffix = uuid4().hex[:8]
    org_a = await _create_org(api, owner, f"it-rev-a-{suffix}")
    org_b = await _create_org(api, reviewer, f"it-rev-b-{suffix}")
    # Membership management is not exposed yet (F7): add the reviewer as the admin path would.
    await _admin_execute(
        env,
        "insert into public.memberships (org_id, user_id, role)"
        " values ($1::uuid, $2::uuid, 'reviewer')",
        UUID(org_a),
        reviewer.id,
    )

    # A real multi-org user: must choose, and sees both orgs.
    me = (await api.get("/me", headers=reviewer.headers)).json()
    assert sorted(org["id"] for org in me["organizations"]) == sorted([org_a, org_b])
    assert (await api.get("/orgs/current", headers=reviewer.headers)).status_code == 400
    in_a = {**reviewer.headers, "X-Org-Id": org_a}
    chosen = await api.get("/orgs/current", headers=in_a)
    assert (chosen.json()["id"], chosen.json()["role"]) == (org_a, "reviewer")

    # Layer 1: require_role.
    assert (await api.patch("/orgs/current", headers=in_a, json={"name": "x"})).status_code == 403

    # Layer 2: call the service directly, skipping require_role; RLS must still refuse.
    with pytest.raises(orgs_service.OrgNotFoundError):
        await orgs_service.rename_organization(await _verified(env, reviewer), UUID(org_a), "x")

    unchanged = (await api.get("/orgs/current", headers=owner.headers)).json()
    assert unchanged["name"] == f"Org it-rev-a-{suffix}"


async def test_api_sessions_run_as_authenticated_with_limits(
    api: httpx.AsyncClient, env: Env, make_user: Callable[[str], Coroutine[Any, Any, User]]
) -> None:
    user_a, user_b = await make_user("rls-a"), await make_user("rls-b")
    suffix = uuid4().hex[:8]
    await _create_org(api, user_a, f"it-rls-a-{suffix}")
    await _create_org(api, user_b, f"it-rls-b-{suffix}")
    verified = await _verified(env, user_a)

    async def probe(session: AsyncSession) -> dict[str, Any]:
        row = (
            await session.execute(
                text(
                    "select current_user as current_user, session_user as session_user,"
                    " current_setting('statement_timeout') as statement_timeout,"
                    " current_setting('idle_in_transaction_session_timeout') as idle_timeout,"
                    " current_setting('lock_timeout') as lock_timeout,"
                    " current_setting('request.jwt.claims', true) as claims,"
                    " (select count(*) from public.organizations where slug like 'it-rls-%')"
                    "   as orgs,"
                    " (select count(*) from public.memberships) as memberships"
                )
            )
        ).one()
        return dict(row._mapping)

    seen = await db.run_as_user(verified.claims, probe)

    assert (seen["current_user"], seen["session_user"]) == ("authenticated", "po_api")
    assert seen["statement_timeout"] == "8s"
    # No external I/O inside run_as_user: an idle transaction is killed after 15 s.
    assert seen["idle_timeout"] == "15s"
    assert seen["lock_timeout"] == "3s"
    # Both test orgs exist, but A's session sees only A's org and A's own membership.
    assert (seen["orgs"], seen["memberships"]) == (1, 1)
    forwarded = json.loads(seen["claims"])
    assert forwarded["sub"] == str(user_a.id)
    assert "user_metadata" not in forwarded
    assert "email" not in forwarded


async def test_role_and_claims_do_not_leak_between_pooled_requests(
    env: Env,
    make_user: Callable[[str], Coroutine[Any, Any, User]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await make_user("pool")
    verified = await _verified(env, user)
    # One connection, so every transaction below reuses the same backend.
    engine = create_async_engine(
        db.to_async_url(env.api_db), pool_size=1, max_overflow=0, hide_parameters=True
    )
    monkeypatch.setattr(db, "get_sessionmaker", lambda: async_sessionmaker(engine))

    async def fail(session: AsyncSession) -> None:
        await session.execute(text("select 1"))
        raise RuntimeError("boom")

    async def ok(session: AsyncSession) -> int:
        return int((await session.execute(text("select 1"))).scalar_one())

    try:
        with pytest.raises(RuntimeError):
            await db.run_as_user(verified.claims, fail)
        assert await db.run_as_user(verified.claims, ok) == 1

        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text("select current_user, current_setting('request.jwt.claims', true)")
                )
            ).one()
        assert row[0] == "po_api"
        assert row[1] in ("", None)
    finally:
        await engine.dispose()


async def test_lock_wait_fails_fast_as_503_busy(
    api: httpx.AsyncClient, env: Env, make_user: Callable[[str], Coroutine[Any, Any, User]]
) -> None:
    user = await make_user("busy")
    # Hold the per-user creation lock, as a slow concurrent create_organization would.
    holder = await asyncpg.connect(env.admin_db)
    try:
        await holder.execute(
            "select pg_advisory_lock(hashtextextended('create_organization:' || $1::text, 0))",
            str(user.id),
        )
        started = time.perf_counter()
        response = await api.post(
            "/orgs",
            headers=user.headers,
            json={"name": "Busy", "slug": f"it-busy-{uuid4().hex[:8]}"},
        )
        elapsed = time.perf_counter() - started
    finally:
        await holder.close()

    assert (response.status_code, response.json()) == (503, {"detail": "busy"})
    assert response.headers["retry-after"] == "2"
    assert elapsed < 6  # lock_timeout (3 s), not statement_timeout (8 s)


async def test_po_api_has_no_access_without_switching_role(env: Env) -> None:
    conn = await asyncpg.connect(env.api_db)
    try:
        assert await conn.fetchval("select current_user") == "po_api"
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetchval("select count(*) from public.organizations")
        for statement in ("set role postgres", "set role service_role", "set role supabase_admin"):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(statement)
    finally:
        await conn.close()


async def test_requests_without_a_valid_token_are_rejected(api: httpx.AsyncClient) -> None:
    assert (await api.get("/me")).status_code == 401
    assert (await api.get("/me", headers={"Authorization": "Bearer not-a-jwt"})).status_code == 401

"""The admin tool against the real stack: invited user → PostgREST RPC → membership + audit.

Needs local Supabase with PostgREST (see ci.yml `db` job): INTEGRATION_DATABASE_URL,
INTEGRATION_SUPABASE_URL, INTEGRATION_SUPABASE_SECRET_KEY. Mailpit is not needed: the
invited user is created with `admin/generate_link` (same not-yet-accepted user as an
emailed invite, no email sent). Audit rows are append-only, so this test leaves them
behind (ids of deleted users) in the throwaway database.
"""

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import asyncpg
import httpx
import pytest

from tests.integration_support import require_env
from tools import add_member as tool

pytestmark = pytest.mark.integration


@pytest.fixture
def stack() -> Iterator[tuple[str, str, str]]:
    yield (
        require_env("INTEGRATION_SUPABASE_URL").rstrip("/"),
        require_env("INTEGRATION_SUPABASE_SECRET_KEY"),
        require_env("INTEGRATION_DATABASE_URL"),
    )


async def _post(gotrue: httpx.AsyncClient, path: str, headers: dict[str, str], body: Any) -> Any:
    response = await gotrue.post(path, headers=headers, json=body)
    response.raise_for_status()
    return response.json()


async def test_invited_user_is_added_to_an_existing_org_with_an_audit_row(
    stack: tuple[str, str, str],
) -> None:
    url, secret, admin_db = stack
    suffix = uuid4().hex[:8]
    slug = f"it-tool-{suffix}"
    invited_email = f"it-tool-invited-{suffix}@integration.test"
    admin = {"apikey": secret, "authorization": f"Bearer {secret}"}
    user_ids: list[str] = []

    async with httpx.AsyncClient(base_url=url, timeout=10) as gotrue:
        conn = await asyncpg.connect(admin_db)
        try:
            owner = await _post(
                gotrue,
                "/auth/v1/admin/users",
                admin,
                {
                    "email": f"it-tool-owner-{suffix}@integration.test",
                    "password": f"Owner-{uuid4()}",
                    "email_confirm": True,
                },
            )
            user_ids.append(owner["id"])
            # An invited user who has not accepted yet: the tool must work before sign-in.
            invited = await _post(
                gotrue,
                "/auth/v1/admin/generate_link",
                admin,
                {"type": "invite", "email": invited_email},
            )
            user_ids.append(invited["id"])

            org_id = await conn.fetchval(
                "insert into public.organizations (name, slug, created_by)"
                " values ('Tool IT', $1, $2::uuid) returning id",
                slug,
                owner["id"],
            )
            await conn.execute(
                "insert into public.memberships (org_id, user_id, role)"
                " values ($1, $2::uuid, 'owner')",
                org_id,
                owner["id"],
            )

            target = tool.resolve_target(
                {"ADMIN_SUPABASE_URL": url, "ADMIN_SUPABASE_SECRET_KEY": secret}, None
            )
            added = tool.add_member(
                target,
                email=invited_email.upper(),
                org_slug=slug,
                role="reviewer",
                actor="integration@po-agent.test",
                reason="Integration: second buyer",
            )

            assert added.user_id == invited["id"]
            assert added.org_name == "Tool IT"
            role = await conn.fetchval(
                "select role from public.memberships where org_id = $1 and user_id = $2::uuid",
                org_id,
                invited["id"],
            )
            assert role == "reviewer"
            audit = await conn.fetchrow(
                "select actor, reason, details from private.admin_audit_log"
                " where org_id = $1 and target_user_id = $2::uuid",
                org_id,
                invited["id"],
            )
            assert audit is not None
            assert (audit["actor"], audit["reason"]) == (
                "integration@po-agent.test",
                "Integration: second buyer",
            )

            with pytest.raises(tool.ToolError, match="already a member"):
                tool.add_member(
                    target,
                    email=invited_email,
                    org_slug=slug,
                    role="admin",
                    actor="integration@po-agent.test",
                    reason="Integration: twice",
                )
        finally:
            await conn.execute("delete from public.organizations where slug = $1", slug)
            await conn.close()
            for user_id in user_ids:
                await gotrue.delete(f"/auth/v1/admin/users/{user_id}", headers=admin)

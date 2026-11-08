"""set_api_role_password against the local stack: the verifier really lets po_api log in.

Restores the local well-known password afterwards (other integration tests use it).
"""

from urllib.parse import quote

import asyncpg
import pytest

from tests.integration_support import require_env
from tools import set_api_role_password as tool

pytestmark = pytest.mark.integration

TEMP_PASSWORD = "It-Temp#Pw9!vT2xR7$mL4&pZ8"


async def test_a_scram_verifier_sets_a_working_password() -> None:
    admin_url = require_env("INTEGRATION_DATABASE_URL")
    api_url = require_env("INTEGRATION_API_DATABASE_URL")
    host_part = api_url.rsplit("@", 1)[1]

    try:
        await tool.apply_verifier(admin_url, None, tool.scram_verifier(TEMP_PASSWORD))

        conn = await asyncpg.connect(
            f"postgresql://po_api:{quote(TEMP_PASSWORD, safe='')}@{host_part}"
        )
        assert await conn.fetchval("select current_user") == "po_api"
        await conn.close()

        with pytest.raises(asyncpg.InvalidPasswordError):
            await asyncpg.connect(api_url)  # the old (local) password no longer works
    finally:
        await tool.apply_verifier(admin_url, None, tool.scram_verifier(tool.LOCAL_PASSWORD))

    conn = await asyncpg.connect(api_url)
    await conn.close()

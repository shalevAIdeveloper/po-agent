"""set_api_role_password against the local stack: the verifier really lets po_api log in.

Restores the local well-known password afterwards (other integration tests use it).
"""

import asyncio
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


def test_generate_end_to_end_on_the_local_stack(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """--generate sets a password that works through the printed po_api connection string."""
    admin_url = require_env("INTEGRATION_DATABASE_URL")
    api_url = require_env("INTEGRATION_API_DATABASE_URL")
    generated = tool.generate_password()
    monkeypatch.setattr(tool, "generate_password", lambda: generated)

    try:
        assert tool.main(["--generate"], prompt=lambda _msg: admin_url) == 0
        out = capsys.readouterr().out
        printed_url = next(
            line.split(": ", 1)[1].strip()
            for line in out.splitlines()
            if "po_api connection string:" in line
        )

        async def login(url: str) -> str:
            conn = await asyncpg.connect(url)
            try:
                return str(await conn.fetchval("select current_user"))
            finally:
                await conn.close()

        assert asyncio.run(login(printed_url)) == "po_api"
    finally:
        asyncio.run(tool.apply_verifier(admin_url, None, tool.scram_verifier(tool.LOCAL_PASSWORD)))

    async def still_local() -> None:
        conn = await asyncpg.connect(api_url)
        await conn.close()

    asyncio.run(still_local())

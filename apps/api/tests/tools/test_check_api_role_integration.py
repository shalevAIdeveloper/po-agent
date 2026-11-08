"""check_api_role against the local stack's po_api (INTEGRATION_API_DATABASE_URL)."""

import pytest

from tests.integration_support import require_env
from tools import check_api_role as tool

pytestmark = pytest.mark.integration


async def test_local_po_api_passes_every_check() -> None:
    target = tool.parse_target(require_env("INTEGRATION_API_DATABASE_URL"))

    results = {r.name: r for r in await tool.run_checks(target)}

    failing = {name: r.detail for name, r in results.items() if r.status == "FAIL"}
    assert failing == {}
    assert results["no access of its own"].status == "PASS"
    assert results["member of authenticated only (SET, not INHERIT)"].status == "PASS"
    assert results["switch to authenticated (RLS applies)"].status == "PASS"
    assert results["role timeouts"].status == "PASS"
    assert results["local password rejected"].status == "SKIP"
    assert results["TEMP privilege (A4)"].status == "INFO"

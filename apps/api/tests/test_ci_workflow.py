"""Guards the CI hardening in .github/workflows/ci.yml (no YAML dependency on purpose)."""

import re
from pathlib import Path

import pytest

CI_WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "ci.yml"
_PINNED = re.compile(r"^[\w.-]+/[\w.-]+(/[\w./-]+)?@[0-9a-f]{40}$")


@pytest.fixture(scope="module")
def workflow() -> str:
    return CI_WORKFLOW.read_text(encoding="utf-8")


def test_actions_are_pinned_to_commit_shas(workflow: str) -> None:
    refs = re.findall(r"uses:\s*(\S+)", workflow)

    assert refs
    assert [ref for ref in refs if not _PINNED.match(ref)] == []


def test_default_token_permissions_are_read_only(workflow: str) -> None:
    assert re.search(r"^permissions:\n  contents: read\n", workflow, re.MULTILINE)
    assert "write" not in workflow


def test_checkout_does_not_persist_credentials(workflow: str) -> None:
    checkouts = re.findall(
        r"uses: actions/checkout@\S+.*\n\s+with: \{ persist-credentials: false[ ,]", workflow
    )

    assert len(checkouts) == workflow.count("uses: actions/checkout@")

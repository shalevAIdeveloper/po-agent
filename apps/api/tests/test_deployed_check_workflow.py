"""Guards .github/workflows/deployed-check.yml (A13 / ADR 0009; no YAML dependency)."""

import re
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "deployed-check.yml"
_PINNED = re.compile(r"^[\w.-]+/[\w.-]+(/[\w./-]+)?@[0-9a-f]{40}$")


@pytest.fixture(scope="module")
def workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _code(workflow: str) -> str:
    return "\n".join(line for line in workflow.splitlines() if not line.lstrip().startswith("#"))


def test_runs_only_by_hand(workflow: str) -> None:
    # No push / pull_request: it must never become a check suite on every dev push,
    # which Railway's checkSuites gate would wait for (and hit the deployed site).
    on_block = workflow.split("\non:\n", 1)[1].split("\n\n", 1)[0]
    triggers = [
        line.strip()
        for line in on_block.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    assert triggers == ["workflow_dispatch:"]


def test_only_from_dev(workflow: str) -> None:
    assert "if: github.ref == 'refs/heads/dev'" in workflow


def test_target_is_the_fixed_dev_origin(workflow: str) -> None:
    code = _code(workflow)

    assert re.search(
        r"^      DEPLOYED_WEB_URL: https://web-dev-3fce\.up\.railway\.app$", code, re.MULTILINE
    )
    assert "inputs" not in code


def test_no_secrets_and_read_only_token(workflow: str) -> None:
    code = _code(workflow)

    assert "secrets." not in code
    assert re.search(r"^permissions:\n  contents: read\n", workflow, re.MULTILINE)
    assert "write" not in code


def test_actions_are_pinned_and_checkout_keeps_no_credentials(workflow: str) -> None:
    refs = re.findall(r"uses:\s*(\S+)", workflow)

    assert refs
    assert [ref for ref in refs if not _PINNED.match(ref)] == []
    assert workflow.count("persist-credentials: false") == workflow.count("uses: actions/checkout@")


def test_cache_is_restore_only(workflow: str) -> None:
    caches = re.findall(r"uses: (actions/cache\S*)@", workflow)

    assert caches == ["actions/cache/restore"]


def test_runs_the_deployed_check(workflow: str) -> None:
    assert "run: pnpm test:deployed" in workflow

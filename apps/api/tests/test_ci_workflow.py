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


def test_db_job_runs_pgtap_and_integration_tests_with_a_database(workflow: str) -> None:
    db_job = workflow.split("\n  db:\n", 1)[1].split("\n  evals:\n", 1)[0]

    assert "supabase test db" in db_job
    assert re.search(r"INTEGRATION_DATABASE_URL: postgresql://\S+", db_job)
    assert re.search(r"INTEGRATION_API_DATABASE_URL: postgresql://po_api:\S+", db_job)
    for name in ("URL", "PUBLISHABLE_KEY", "SECRET_KEY"):
        assert f"INTEGRATION_SUPABASE_{name}=" in db_job
    # Auth must run for the real-user integration tests; `db start` would start Postgres only.
    excludes = re.search(r"supabase start\s+-x\s+([\w,-]+)", db_job)
    assert excludes, "db job must use `supabase start -x ...`"
    excluded = set(excludes.group(1).split(","))
    assert not excluded & {"gotrue", "kong"}, "auth (gotrue behind kong) must not be excluded"
    assert re.search(r"pytest -m integration\b.*-rs", db_job)


def test_every_job_is_covered_by_ci_ok(workflow: str) -> None:
    jobs_section = workflow.split("\njobs:\n", 1)[1]
    # A job header may carry trailing spaces or a comment (`  deploy:  # prod`).
    jobs = set(re.findall(r"^  ([\w-]+):[ \t]*(?:#.*)?\n", jobs_section, re.MULTILINE))
    needs = re.search(r"^  ci-ok:\n(?:.*\n)*?    needs: \[([^\]]+)\]", jobs_section, re.MULTILINE)

    assert needs, "ci-ok must list its needs inline"
    assert {"api", "web", "db", "images"} <= jobs
    assert {job.strip() for job in needs.group(1).split(",")} == jobs - {"ci-ok"}


def test_images_job_builds_and_smoke_tests_the_deploy_images(workflow: str) -> None:
    images_job = workflow.split("\n  images:\n", 1)[1].split("\n  evals:\n", 1)[0]

    assert "bash .github/scripts/smoke-images.sh" in images_job
    assert "pnpm typecheck" in images_job
    # Deploy-relevant paths must trigger it, including the IaC file.
    assert re.search(r"images: \[.*'apps/api/\*\*'.*'apps/web/\*\*'.*'\.railway/\*\*'", workflow)


def test_runs_on_deploy_branches_are_never_cancelled(workflow: str) -> None:
    # Railway deploys a dev push only after its CI passed (checkSuites); a cancelled
    # run would skip that commit's deploy for good (sec, PR #12).
    concurrency = re.search(r"^concurrency:\n((?:  .*\n)+)", workflow, re.MULTILINE)

    assert concurrency
    assert re.search(
        r"^  cancel-in-progress: \$\{\{ github\.event_name == 'pull_request' \}\}$",
        concurrency.group(1),
        re.MULTILINE,
    )


def test_every_push_gets_a_ci_run(workflow: str) -> None:
    # No workflow-level path filters: Railway's checkSuites gate needs a CI suite on
    # every dev push, or it deploys without waiting (sec, PR #12).
    block = workflow.split("\non:\n", 1)[1].split("\n\n", 1)[0]
    triggers = [line for line in block.splitlines() if not line.lstrip().startswith("#")]

    assert "  push:" in triggers
    assert not [line for line in triggers if "paths" in line]


def test_web_job_checks_generated_api_types_are_committed_and_current(workflow: str) -> None:
    web_job = workflow.split("\n  web:\n", 1)[1].split("\n  db:\n", 1)[0]

    assert "git ls-files --error-unmatch src/lib/api/schema.d.ts" in web_job
    assert "pnpm gen:api" in web_job
    assert "git status --porcelain -- src/lib/api/schema.d.ts" in web_job

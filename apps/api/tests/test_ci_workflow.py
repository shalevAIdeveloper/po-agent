"""Guards the CI hardening in .github/workflows/ci.yml (no YAML dependency on purpose)."""

import json
import os
import re
import shutil
import subprocess
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


def _ci_ok_job(workflow: str) -> str:
    return workflow.split("\n  ci-ok:\n", 1)[1]


def _ci_ok_jq_program(workflow: str) -> str:
    match = re.search(r"jq -e '\n(.*?)\n\s*' > /dev/null", _ci_ok_job(workflow), re.DOTALL)
    assert match, "ci-ok must check needs with an inline `jq -e '...'` program"
    return match.group(1)


def test_m1_engine_gets_pull_request_ci_but_no_push_trigger(workflow: str) -> None:
    block = workflow.split("\non:\n", 1)[1].split("\n\n", 1)[0]

    assert re.search(
        r"^  pull_request:\n(?:    #.*\n)*"
        r"    types: \[opened, synchronize, reopened, edited\]\n"
        r"    branches: \[dev, main, m1/engine\]$",
        block,
        re.MULTILINE,
    )
    assert re.search(r"^  push:\n    branches: \[dev, main\]$", block, re.MULTILINE)


def test_base_branch_change_re_runs_ci(workflow: str) -> None:
    # sec H1: a ci-ok computed for a PR into m1/engine (evals exception on) must not stay
    # attached when the PR is retargeted to dev; `edited` fires on a base change.
    block = workflow.split("\non:\n", 1)[1].split("\n  push:\n", 1)[0]
    types = re.search(r"^    types: \[([^\]]+)\]$", block, re.MULTILINE)

    assert types
    assert "edited" in {t.strip() for t in types.group(1).split(",")}


def test_evals_exception_is_limited_to_m1_engine_before_the_baseline(workflow: str) -> None:
    ci_ok = _ci_ok_job(workflow)
    flag = re.findall(r"^\s+EVALS_MAY_FAIL: (.+)$", ci_ok, re.MULTILINE)

    assert flag == [
        "${{ github.event_name == 'pull_request' && github.base_ref == 'm1/engine' "
        "&& needs.changes.outputs.evals_baseline == 'false' }}"
    ]
    assert "evals_baseline: ${{ steps.baseline.outputs.exists }}" in workflow
    changes_job = workflow.split("\n  changes:\n", 1)[1].split("\n  api:\n", 1)[0]
    assert (
        "      - id: baseline\n"
        "        run: |\n"
        '          if [ -f apps/api/evals/baseline.json ]; then echo "exists=true"; '
        'else echo "exists=false"; fi >> "$GITHUB_OUTPUT"\n'
    ) in changes_job
    assert "continue-on-error" not in changes_job
    # Expressions compare case-insensitively; the shell re-checks the base ref exactly.
    assert (
        'if [ "$GITHUB_BASE_REF" != "m1/engine" ]; then export EVALS_MAY_FAIL=false; fi'
    ) in ci_ok
    # The exception names evals, a failure only, and the flag; nothing else may be excused.
    program = _ci_ok_jq_program(workflow)
    assert program.count("env.EVALS_MAY_FAIL") == 1
    assert '.key == "evals" and .value.result == "failure"' in program


_ALL_GREEN = {"changes": "success", "api": "success", "web": "skipped", "evals": "success"}


# Skipped only on a dev machine without jq; in CI (`CI=true`) a missing jq fails the test.
@pytest.mark.skipif(
    shutil.which("jq") is None and not os.environ.get("CI"),
    reason="jq not installed locally (CI runners have it)",
)
@pytest.mark.parametrize(
    ("results", "may_fail", "passes"),
    [
        (_ALL_GREEN, "false", True),
        ({**_ALL_GREEN, "evals": "failure"}, "false", False),  # dev/main, or baseline exists
        ({**_ALL_GREEN, "evals": "failure"}, "true", True),  # m1/engine before PR 9
        ({**_ALL_GREEN, "evals": "cancelled"}, "true", False),
        ({**_ALL_GREEN, "api": "failure"}, "true", False),  # only evals is excused
        ({**_ALL_GREEN, "changes": "failure"}, "true", False),
        ({**_ALL_GREEN, "changes": "skipped"}, "true", False),
        ({k: v for k, v in _ALL_GREEN.items() if k != "changes"}, "true", False),
        ({**_ALL_GREEN, "evals": "failure", "api": "failure"}, "true", False),
        ({**_ALL_GREEN, "evals": "failure", "db": "failure"}, "true", False),
        ({**_ALL_GREEN, "evals": "failure"}, "", False),  # flag must be exactly "true"
        ({**_ALL_GREEN, "evals": "failure"}, "True", False),
        ({**_ALL_GREEN, "evals": "skipped"}, "true", True),
    ],
)
def test_ci_ok_verdicts(
    workflow: str, results: dict[str, str], may_fail: str, passes: bool
) -> None:
    needs = json.dumps({job: {"result": result} for job, result in results.items()})
    jq = shutil.which("jq")
    assert jq
    proc = subprocess.run(  # noqa: S603 - fixed jq binary, program from our own workflow
        [jq, "-e", _ci_ok_jq_program(workflow)],
        input=needs,
        capture_output=True,
        text=True,
        env={**os.environ, "EVALS_MAY_FAIL": may_fail},
        check=False,
    )

    assert (proc.returncode == 0) is passes, proc.stderr


_EVALS_COMMAND = "uv run python -m evals.run --replay --fail-on-regression"


def _evals_job(workflow: str) -> str:
    return workflow.split("\n  evals:\n", 1)[1].split("\n  ci-ok:\n", 1)[0]


def test_evals_job_uses_no_secret(workflow: str) -> None:
    # Owner decision Q9: CI evals replay recordings; no key, no network (M1 PR 0b).
    job = _evals_job(workflow)

    assert "secrets." not in job
    assert "LLM_API_KEY" not in job
    assert "vars." not in job
    assert "github.token" not in job
    assert "permissions:" not in job  # inherits the read-only default, nothing more
    # No env at any level of the job (job, step, or a nested block); sec L4.
    assert not re.search(r"^\s*env:", job, re.MULTILINE)
    # Nor one at workflow level, which every job would inherit.
    assert not re.search(r"^env:", workflow, re.MULTILINE)


def test_evals_job_runs_a_strict_replay(workflow: str) -> None:
    job = _evals_job(workflow)
    runs = re.findall(r"^      - run: (.+)$", job, re.MULTILINE)

    assert runs == ["uv sync --frozen", _EVALS_COMMAND]
    assert "continue-on-error" not in job
    assert "|| true" not in job
    assert (
        "    if: needs.changes.outputs.evals == 'true' && github.event_name == 'pull_request'\n"
    ) in job
    assert "    needs: changes\n" in job


def test_evals_path_filter_covers_the_engine_but_not_workflows_yet(workflow: str) -> None:
    changes_job = workflow.split("\n  changes:\n", 1)[1].split("\n  api:\n", 1)[0]
    match = re.search(r"^            evals: \[([^\]]*)\]$", changes_job, re.MULTILINE)

    assert match
    paths = {p.strip().strip("'") for p in match.group(1).split(",")}
    assert paths == {
        "apps/api/app/prompts/**",
        "apps/api/app/extraction/**",
        "apps/api/app/llm/**",
        "apps/api/evals/**",
    }
    # G2 (`.github/workflows/**`) waits for PR 8: PRs into dev have no evals exception.
    assert not [p for p in paths if p.startswith(".github")]


def test_evals_stays_a_required_part_of_ci_ok(workflow: str) -> None:
    needs = re.search(r"^  ci-ok:\n(?:.*\n)*?    needs: \[([^\]]+)\]", workflow, re.MULTILINE)

    assert needs
    assert "evals" in {job.strip() for job in needs.group(1).split(",")}


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

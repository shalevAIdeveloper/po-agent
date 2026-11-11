"""Guards what `railway up apps/<app>` uploads (.railwayignore at the root and per app).

Railway filters the upload with gitignore rules (.gitignore + .railwayignore). In that
syntax a bare `supabase/` matches a folder of that name at any depth: in 0.5c it dropped
apps/web/src/lib/supabase/ and the web build failed. These tests run git's own matcher
over the tracked files, so a rule that hides needed code (or misses a secret) fails here
instead of in a cloud build. CI runs them whenever apps/web or any ignore file changes.

Model limits: git gives .gitignore (incl. negations like `!.env.example`) precedence over
core.excludesFile; Railway's exact merge order is not documented. Per-app rules are
treated as matching at any depth, which is exact because they contain no inner "/"
(asserted below).
"""

import fnmatch
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
GIT = shutil.which("git")

if GIT is None or not (ROOT / ".git").exists():
    if os.environ.get("CI"):
        pytest.fail("test_railway_upload needs git and the repo checkout in CI", pytrace=False)
    pytest.skip("needs git and the repo checkout", allow_module_level=True)

# Only .railwayignore (not .gitignore) excludes these: proves the rules were loaded.
CANARY = {"api": "apps/api/tests/canary_test.py", "web": "apps/web/e2e/canary.spec.ts"}

FORBIDDEN = [
    ".env",
    ".env.local",
    "apps/api/.env",
    "apps/api/.env.production",
    "apps/web/.env.local",
    "apps/web/src/.env",
    "supabase/roles.sql",
    "supabase/signing_keys.json",
    "apps/api/app/signing_keys.json",
    "apps/api/server.pem",
    "apps/api/app/server.key",
    "apps/web/prod-ca-2021.crt",
    "apps/web/certs/ca.txt",
    "apps/web/.npmrc",
    "apps/api/.envrc",
    "apps/api/tools/check_api_role.py",
    "apps/api/tests/test_x.py",
    "apps/api/evals/fixtures/po.pdf",
    "apps/web/e2e/login.spec.ts",
    "apps/web/src/lib/x.test.ts",
    "apps/web/node_modules/next/package.json",
    "apps/web/.next/server/app.js",
    "apps/api/.venv/bin/python",
    "apps/api/app/__pycache__/main.cpython-312.pyc",
]


def _git(*args: str, stdin: list[str] | None = None) -> list[str]:
    """Runs git with NUL-separated input/output (bytes: Windows text mode would turn
    "\\n" into "\\r\\n" and git would match nothing)."""
    assert GIT is not None
    data = "".join(f"{p}\0" for p in stdin or []).encode()
    result = subprocess.run(  # noqa: S603 (fixed git arguments, no shell)
        [GIT, *args], cwd=ROOT, input=data, capture_output=True, check=False
    )
    # check-ignore exits 1 when nothing is ignored
    assert result.returncode in (0, 1), result.stderr.decode(errors="replace")
    return [p for p in result.stdout.decode().split("\0") if p]


def _excludes_file(app: str, tmp_path: Path) -> Path:
    """Root .railwayignore + the app's, as one excludes file for git."""
    app_rules = (ROOT / "apps" / app / ".railwayignore").read_text(encoding="utf-8")
    for line in app_rules.splitlines():
        rule = line.strip().removeprefix("**/").rstrip("/")
        if rule and not rule.startswith("#"):
            assert "/" not in rule, f"apps/{app}/.railwayignore: anchored rule {line!r}"
    combined = tmp_path / f"railwayignore-{app}"
    combined.write_text(
        (ROOT / ".railwayignore").read_text(encoding="utf-8") + "\n" + app_rules,
        encoding="utf-8",
    )
    return combined


def _ignored(app: str, paths: list[str], tmp_path: Path) -> set[str]:
    canary = CANARY[app]
    assert _git("check-ignore", "--no-index", "-z", "--stdin", stdin=[canary]) == [], (
        f"{canary} is already ignored by .gitignore; pick another canary"
    )
    found = _git(
        "-c",
        f"core.excludesFile={_excludes_file(app, tmp_path).as_posix()}",
        "check-ignore",
        "--no-index",
        "-z",
        "--stdin",
        stdin=[*paths, canary],
    )
    assert canary in found, "git did not apply the .railwayignore rules"
    return set(found) - {canary}


def _tracked(prefix: str) -> list[str]:
    return _git("ls-files", "-z", "--", prefix)


def _needed(app: str) -> list[str]:
    """Tracked files the image build takes: the `!` allow-list of apps/<app>/.dockerignore
    (minus tests), plus the Dockerfile and .dockerignore themselves."""
    base = f"apps/{app}/"
    dockerignore = (ROOT / base / ".dockerignore").read_text(encoding="utf-8")
    allowed = [ln[1:].strip().rstrip("/") for ln in dockerignore.splitlines() if ln[:1] == "!"]
    assert allowed, f"{base}.dockerignore has no allow-list"
    needed = [
        path
        for path in _tracked(base)
        if any(fnmatch.fnmatch(path[len(base) :].split("/")[0], pat) for pat in allowed)
        and ".test." not in path
    ]
    assert any("/" in path[len(base) :] for path in needed), f"no tracked code under {base}"
    return [*needed, f"{base}Dockerfile", f"{base}.dockerignore"]


@pytest.mark.parametrize("app", ["api", "web"])
def test_needed_files_are_uploaded(app: str, tmp_path: Path) -> None:
    needed = _needed(app)

    assert sorted(_ignored(app, needed, tmp_path)) == []


def test_web_supabase_helpers_are_uploaded(tmp_path: Path) -> None:
    # The exact files whose absence broke the first cloud build.
    helpers = ["apps/web/src/lib/supabase/proxy.ts", "apps/web/src/lib/supabase/server.ts"]
    assert set(helpers) <= set(_needed("web"))

    assert _ignored("web", helpers, tmp_path) == set()


@pytest.mark.parametrize("app", ["api", "web"])
def test_secrets_tools_and_build_output_are_not_uploaded(app: str, tmp_path: Path) -> None:
    assert sorted(set(FORBIDDEN) - _ignored(app, FORBIDDEN, tmp_path)) == []

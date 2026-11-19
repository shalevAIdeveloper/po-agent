"""Deploy images: every base image is pinned by digest (ADR 0008; sec, PR #12 #9), and
the image smoke script keeps its API-image contents checks (ADR 0012)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DOCKERFILES = [ROOT / "apps" / "api" / "Dockerfile", ROOT / "apps" / "web" / "Dockerfile"]
SMOKE_SCRIPT = ROOT / ".github" / "scripts" / "smoke-images.sh"
_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


def _api_contents_check() -> str:
    # The python heredoc run inside the API image by the "api image: contents" step.
    script = SMOKE_SCRIPT.read_text(encoding="utf-8")
    step = script.split('echo "--- api image: contents"', 1)[1]
    step = step.split('|| fail "api image contents"', 1)[0]
    return step.split("python - <<PY\n", 1)[1].split("\nPY\n", 1)[0]


@pytest.mark.parametrize(
    "module", ["opentelemetry.sdk", "fastapi_cli", "pytest", "reportlab", "lxml", "pandas", "numpy"]
)
def test_api_image_smoke_asserts_module_is_absent(module: str) -> None:
    check = _api_contents_check()
    absent = re.findall(r"for mod in \(([^)]*)\):\n\s+assert u\.find_spec\(mod\) is None", check)

    assert any(f'"{module}"' in names for names in absent)


def test_api_image_smoke_asserts_openpyxl_parses_through_defusedxml() -> None:
    # ADR 0012: openpyxl falls back to the stdlib parser (no XXE / billion-laughs guard)
    # if defusedxml is missing, and prefers lxml if present; the image must have neither risk.
    check = _api_contents_check()

    assert re.search(r"^import defusedxml$", check, re.MULTILINE)
    assert re.search(r"^import openpyxl\.xml$", check, re.MULTILINE)
    assert re.search(r"^assert openpyxl\.xml\.DEFUSEDXML is True\b", check, re.MULTILINE)
    assert re.search(r"^assert openpyxl\.xml\.LXML is False\b", check, re.MULTILINE)
    # sec L6: the flags alone don't prove iterparse/fromstring come from defusedxml.
    assert re.search(
        r"^from app\.extraction\.inputs\.xlsx import xml_is_hardened$", check, re.MULTILINE
    )
    assert re.search(r"^assert xml_is_hardened\(\), ", check, re.MULTILINE)


def test_smoke_hardening_check_names_a_real_function() -> None:
    # A renamed function would only fail in the image job; catch it in unit tests.
    from app.extraction.inputs.xlsx import xml_is_hardened

    assert xml_is_hardened() is True


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: p.parent.name)
def test_syntax_frontend_is_pinned_by_digest(dockerfile: Path) -> None:
    # Railway builds every push to dev unattended; a floating `docker/dockerfile:1`
    # would fetch whatever frontend that tag points to at build time.
    first = dockerfile.read_text(encoding="utf-8").splitlines()[0]
    syntax = re.fullmatch(r"# syntax=(\S+)", first)

    assert syntax, "the first line must be the # syntax directive"
    assert re.match(r"docker/dockerfile:[\d.]+@sha256:", syntax.group(1))
    assert _DIGEST.search(syntax.group(1))


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: p.parent.name)
def test_external_base_images_are_pinned_by_digest(dockerfile: Path) -> None:
    lines = dockerfile.read_text(encoding="utf-8").splitlines()
    stages = {m.group(1) for line in lines if (m := re.match(r"FROM \S+ AS (\S+)", line))}
    images = [m.group(1) for line in lines if (m := re.match(r"FROM (\S+)", line))]
    external = [image for image in images if image not in stages]

    assert external
    assert [image for image in external if not _DIGEST.search(image)] == []

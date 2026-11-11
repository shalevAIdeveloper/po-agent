"""Every image a deploy build pulls is pinned by digest (ADR 0008; sec, PR #12 #9)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DOCKERFILES = [ROOT / "apps" / "api" / "Dockerfile", ROOT / "apps" / "web" / "Dockerfile"]
_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


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

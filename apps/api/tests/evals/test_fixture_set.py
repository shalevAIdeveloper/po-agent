"""The fixture set as a whole (M1 PR 6 T9): complete, synthetic, fictional, small.

- the registry and the fixture directories match; at least 40 cases; the plan's minimum
  per group (owner decision Q1);
- git: every tracked case has a tracked `meta.json` with `synthetic: true`, and nothing
  under `fixtures/private/` is tracked (docs/plans/m1.md, "Real customer documents");
- fictional data (owner rule, PR 6): every email address and URL host in the fixtures,
  the generator and its README is `example.com` or a subdomain of it; every phone number
  is 555-01xx; no number is printed as "1,234" (Q-PR5-7 open);
- sizes: every file under 2 MB (test_fixtures), the whole set under 8 MB.
"""

import io
import re
import shutil
import subprocess
import zipfile
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import pytest

from evals.generate.cases import CASES, by_name
from evals.generate.meta import allowed_domain
from evals.generate.spec import GROUP_MINIMUMS, GROUPS
from evals.run import FIXTURES_DIR
from tests.evals.support import document_text

_API_DIR = Path(__file__).resolve().parents[2]
_REPO_ROOT = _API_DIR.parents[1]
MAX_SET_BYTES = 8 * 1024 * 1024
_THIS_FILE = "test_fixture_set.py"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
_URL_RE = re.compile(r"(?:https?://|www\.)([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<![\d-])(?:\(?\d{3}\)?[ .-]?)?(\d{3})[ .-](\d{4})(?![\d-])")
# A thousands separator with no decimals after it ("1,234"), which Q-PR5-7 leaves open.
_BARE_THOUSANDS_RE = re.compile(r"(?<![\d.,])\d{1,3}(?:,\d{3})+(?![\d,]|\.\d)")
# Reserved names (RFC 2606 / 6761) allowed in tests only, where a refusal test needs a
# domain that is not example.com; plus two made-up lookalikes that prove suffix matching.
_TEST_ONLY_RESERVED = (".invalid", ".test", ".example", ".localhost")
_TEST_ONLY_HOSTS = frozenset({"example.org", "example.net", "notexample.com", "myexample.com"})


def _git(*args: str) -> list[str]:
    git = shutil.which("git")
    assert git, "git is needed (CI's checkout has it)"
    proc = subprocess.run(  # noqa: S603 - git found on PATH, fixed arguments
        [git, *args], cwd=_REPO_ROOT, capture_output=True, text=True, check=True
    )
    return proc.stdout.splitlines()


def _fixture_dirs() -> list[str]:
    return sorted(
        p.name
        for p in FIXTURES_DIR.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name.lower() != "private"
    )


# --- the set -------------------------------------------------------------------------------


def test_the_registry_and_the_fixture_directories_match() -> None:
    assert _fixture_dirs() == sorted(by_name())


def test_there_are_at_least_forty_cases() -> None:
    assert len(CASES) >= 40
    assert len(CASES) == 46  # owner decision Q1; PR 6b adds the rest of the sheet catalogue


def test_every_group_has_its_minimum() -> None:
    counts = Counter(case.group for case in CASES)
    assert set(counts) == set(GROUPS)
    short = {g: (counts[g], m) for g, m in GROUP_MINIMUMS.items() if counts[g] < m}
    assert short == {}


def test_each_input_type_is_covered() -> None:
    assert {case.input_type for case in CASES} == {"pdf", "png", "jpg", "xlsx", "csv", "eml"}


def test_seeds_are_unique() -> None:
    seeds = [case.seed for case in CASES]
    assert len(seeds) == len(set(seeds))


def test_the_whole_set_is_under_eight_megabytes() -> None:
    size = sum(p.stat().st_size for p in FIXTURES_DIR.rglob("*") if p.is_file())
    assert size < MAX_SET_BYTES


# --- git -----------------------------------------------------------------------------------


def test_every_tracked_case_has_a_synthetic_meta() -> None:
    tracked = _git("ls-files", "--", "apps/api/evals/fixtures")
    cases: dict[str, set[str]] = {}
    for path in tracked:
        parts = Path(path).relative_to("apps/api/evals/fixtures").parts
        if len(parts) >= 2:
            cases.setdefault(parts[0], set()).add("/".join(parts[1:]))
    assert len(cases) == len(CASES)  # every case is tracked, and nothing else is
    for name, files in cases.items():
        assert "meta.json" in files, name
        meta = (FIXTURES_DIR / name / "meta.json").read_text(encoding="utf-8")
        assert re.search(r'"synthetic":\s*true', meta), name


def test_nothing_under_private_fixtures_is_tracked() -> None:
    tracked = _git("ls-files", "--", "apps/api/evals")
    private = [p for p in tracked if any(part.lower() == "private" for part in Path(p).parts)]
    assert private == []


# --- fictional data ------------------------------------------------------------------------


_TAG_RE = re.compile(r"<[^>]*>")


def _xlsx_text(data: bytes) -> str:
    """Every part's text with the XML tags removed: namespace URIs (w3.org, purl.org,
    openxmlformats.org) are the format's own, not document data."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return "\n".join(
            _TAG_RE.sub(" ", archive.read(name).decode("utf-8")) for name in archive.namelist()
        )


def _fixture_texts() -> Iterator[tuple[str, str]]:
    """(where, text) for everything a fixture holds: raw sources where they are text,
    the parsed or pre-raster text of every document, expected.json and meta.json."""
    for case in CASES:
        folder = FIXTURES_DIR / case.name
        source = (folder / case.source_name).read_bytes()
        if case.input_type in ("eml", "csv"):
            yield f"{case.name}/source", source.decode("utf-8")
        elif case.input_type == "xlsx":
            yield f"{case.name}/source", _xlsx_text(source)
        text = document_text(case.name)
        if text is not None:
            yield f"{case.name}/text", text
        for name in ("expected.json", "meta.json"):
            yield f"{case.name}/{name}", (folder / name).read_text(encoding="utf-8")


def _source_texts(*folders: str) -> Iterator[tuple[str, str]]:
    for folder in folders:
        for path in sorted((_API_DIR / folder).rglob("*")):
            if path.suffix in (".py", ".md") and "__pycache__" not in path.parts:
                yield str(path.relative_to(_API_DIR)), path.read_text(encoding="utf-8")


def _hosts(text: str) -> set[str]:
    return {m.lower() for m in _EMAIL_RE.findall(text)} | {m.lower() for m in _URL_RE.findall(text)}


def _test_only_allowed(host: str) -> bool:
    return allowed_domain(host) or host in _TEST_ONLY_HOSTS or host.endswith(_TEST_ONLY_RESERVED)


def test_fixture_and_generator_emails_and_urls_are_at_example_com() -> None:
    texts = [*_fixture_texts(), *_source_texts("evals")]  # evals/ includes generate/
    bad = sorted(
        {
            (where, host)
            for where, text in texts
            for host in _hosts(text)
            if not allowed_domain(host)
        }
    )
    assert bad == []


def test_test_emails_and_urls_are_example_com_or_reserved() -> None:
    bad = sorted(
        {
            (where, host)
            for where, text in _source_texts("tests/evals")
            for host in _hosts(text)
            if not _test_only_allowed(host)
        }
    )
    assert bad == []


@pytest.mark.parametrize(
    ("host", "fixture_ok", "test_ok"),
    [
        ("brindlewood.example.com", True, True),
        ("example.com", True, True),
        ("mail.invalid", False, True),
        ("brindlewood.test", False, True),
        ("notexample.com", False, True),
        ("gmail.com", False, False),
        ("example.com.evil.net", False, False),
    ],
)
def test_the_scan_rules_themselves(host: str, fixture_ok: bool, test_ok: bool) -> None:
    assert allowed_domain(host) is fixture_ok
    assert _test_only_allowed(host) is test_ok


def _phones(text: str) -> set[str]:
    return {f"{a}-{b}" for a, b in _PHONE_RE.findall(text)}


def test_every_phone_number_is_555_01xx() -> None:
    # This file is left out: its scan self-tests hold a deliberate non-555-01xx number.
    tests = [(w, t) for w, t in _source_texts("tests/evals") if not w.endswith(_THIS_FILE)]
    texts = [*_fixture_texts(), *_source_texts("evals/generate"), *tests]
    bad = sorted(
        {
            (where, phone)
            for where, text in texts
            for phone in _phones(text)
            if not re.fullmatch(r"555-01\d\d", phone)
        }
    )
    assert bad == []


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("(614) 555-0142", {"555-0142"}),
        ("call 555-0199 today", {"555-0199"}),
        ("614-555-1234", {"555-1234"}),
        ("NP-10422 and 2026-11-12", set()),
    ],
)
def test_the_phone_scan_itself(text: str, found: set[str]) -> None:
    assert _phones(text) == found


def test_no_document_prints_a_thousands_separator_without_decimals() -> None:
    bad = []
    for case in CASES:
        text = document_text(case.name)
        if text is not None:
            bad += [(case.name, m) for m in _BARE_THOUSANDS_RE.findall(text)]
    assert bad == []


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("1,234", ["1,234"]),
        ("1,234.50", []),
        ("12,345,678", ["12,345,678"]),
        ("0.82", []),
        ("Fernhill, OH 45991", []),
        ("3,796.00 and 2.469,00", []),
    ],
)
def test_the_thousands_scan_itself(text: str, found: list[str]) -> None:
    assert _BARE_THOUSANDS_RE.findall(text) == found


def test_each_case_writes_its_seed_once() -> None:
    # dev L4: randomness only from the case's `seed` variable, never a second literal.
    cases = _API_DIR / "evals" / "generate" / "cases"
    literal = re.compile(r"random\.Random\(\s*\d")
    found = [
        p.name for p in sorted(cases.glob("*.py")) if literal.search(p.read_text(encoding="utf-8"))
    ]
    assert found == []

"""Eval runner, M1 PR 0b bootstrap: checks what a replay needs and always fails.

Owner rule (docs/plans/m1.md, PR 0b): the CI `evals` job must never pass silently.
Until scoring exists (PR 7) and a baseline is recorded (PR 9), every outcome exits
non-zero with its own code and one stderr line. Messages never carry document
content, key values, or private case names. Nothing here reads a fixture, opens a
socket, or imports an LLM SDK.

Exit codes (also in evals/README.md):
    2  usage error, or --help (argparse)
    10 --live without a non-empty LLM_API_KEY
    11 --live with a key: not implemented until PR 9
    20 no fixture cases found
    21 a case is invalid or incomplete (exactly one source.*, an expected.json, all
       resolving inside the fixtures root; symlinked case directories are skipped)
    22 a case has no recording under RECORDINGS_DIR (recordings/private/ for private)
    23 no baseline at BASELINE_PATH
    30 everything present, but scoring is not implemented (PR 7)
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NoReturn

_PACKAGE: Final = Path(__file__).resolve().parent
FIXTURES_DIR: Final = _PACKAGE / "fixtures"
RECORDINGS_DIR: Final = _PACKAGE / "recordings"
# ci.yml's `changes.baseline` step checks this same file (m1/engine evals exception).
BASELINE_PATH: Final = _PACKAGE / "baseline.json"
PRIVATE_DIRNAME: Final = "private"

EXIT_USAGE: Final = 2
EXIT_LIVE_NO_KEY: Final = 10
EXIT_LIVE_NOT_IMPLEMENTED: Final = 11
EXIT_NO_FIXTURES: Final = 20
EXIT_INVALID_FIXTURE: Final = 21
EXIT_MISSING_RECORDINGS: Final = 22
EXIT_NO_BASELINE: Final = 23
EXIT_SCORING_NOT_IMPLEMENTED: Final = 30

# Case names are printed; anything else (spaces, customer names, control chars) is not.
_SAFE_NAME: Final = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


@dataclass(frozen=True)
class Case:
    name: str
    path: Path
    private: bool


def _fail(code: int, message: str) -> int:
    print(f"evals: {message}", file=sys.stderr)
    return code


def _names(cases: Sequence[Case], label: str) -> str:
    """Public, safe names only; private and unsafe names are counted, never shown."""
    shown = sorted(c.name for c in cases if not c.private and _SAFE_NAME.fullmatch(c.name))
    private = sum(1 for c in cases if c.private)
    unsafe = sum(1 for c in cases if not c.private and not _SAFE_NAME.fullmatch(c.name))
    parts = [", ".join(shown)] if shown else []
    if private:
        parts.append(f"{private} private")
    if unsafe:
        parts.append(f"{unsafe} with an unsafe name")
    return f"{len(cases)} {label}: " + "; ".join(parts)


def _is_private_name(name: str) -> bool:
    return name.lower() == PRIVATE_DIRNAME


def _discover(fixtures: Path, include_private: bool) -> list[Case]:
    """Cases are the non-hidden, non-symlink subdirectories; `private/` only on request.

    A case is private (name never printed, recording under recordings/private/) when it
    sits in a `private` folder, when any part of the given path is named `private`, or
    when it resolves into FIXTURES_DIR/private (e.g. through a symlinked alias).
    """
    if not fixtures.is_dir():
        return []
    private_root = (FIXTURES_DIR / PRIVATE_DIRNAME).resolve()
    path_is_private = any(_is_private_name(part) for part in fixtures.parts)

    def cases_in(folder: Path, private: bool) -> list[Case]:
        found = sorted(
            p
            for p in folder.iterdir()
            if p.is_dir() and not p.is_symlink() and not p.name.startswith(".")
        )
        return [
            Case(p.name, p, private or path_is_private or p.resolve().is_relative_to(private_root))
            for p in found
            if not _is_private_name(p.name)
        ]

    cases = cases_in(fixtures, private=False)
    if include_private:
        # The private folder itself may be a symlink; its cases then fail the root check.
        for folder in sorted(p for p in fixtures.iterdir() if _is_private_name(p.name)):
            if folder.is_dir():
                cases += cases_in(folder, private=True)
    return cases


def _is_valid(case: Case, root: Path) -> bool:
    """Exactly one source.* and an expected.json, all resolving inside the fixtures root."""
    if not case.private and not _SAFE_NAME.fullmatch(case.name):
        return False
    sources = [p for p in case.path.glob("source.*") if p.is_file()]
    expected = case.path / "expected.json"
    if len(sources) != 1 or not expected.is_file():
        return False
    return all(p.resolve().is_relative_to(root) for p in (case.path, *sources, expected))


def _has_recording(case: Case, recordings: Path) -> bool:
    """A file named `<case>.<ext>`: private cases only under recordings/private/, public
    cases only outside it. Placeholder key: PR 2/7 define the real one (prompt + schema
    + model hash)."""
    if not recordings.is_dir():
        return False
    for p in recordings.rglob("*"):
        in_private = _is_private_name(p.relative_to(recordings).parts[0])
        if in_private == case.private and p.stem == case.name and p.is_file():
            return True
    return False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.run", description="Run the extraction evals."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--replay", action="store_true", help="score recorded answers (CI)")
    mode.add_argument("--live", action="store_true", help="call the LLM (needs LLM_API_KEY)")
    parser.add_argument("--fail-on-regression", action="store_true")
    parser.add_argument("--fixtures", type=Path, default=None, metavar="DIR")
    parser.add_argument(
        "--include-private", action="store_true", help="also run fixtures/private (local only)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit:
        # argparse exits 2 on bad usage and 0 on --help; nothing in this PR may return 0.
        return EXIT_USAGE

    if args.live:
        if not os.environ.get("LLM_API_KEY", "").strip():
            return _fail(EXIT_LIVE_NO_KEY, "--live needs a non-empty LLM_API_KEY")
        return _fail(EXIT_LIVE_NOT_IMPLEMENTED, "live mode not implemented until PR 9")

    fixtures: Path = args.fixtures if args.fixtures is not None else FIXTURES_DIR
    cases = _discover(fixtures, args.include_private)
    if not cases:
        return _fail(EXIT_NO_FIXTURES, "no fixtures found (expected fixtures/<case>/)")

    root = fixtures.resolve()
    invalid = [c for c in cases if not _is_valid(c, root)]
    if invalid:
        return _fail(
            EXIT_INVALID_FIXTURE,
            _names(invalid, "case(s) invalid or incomplete (need one source.*, expected.json)"),
        )

    missing = [c for c in cases if not _has_recording(c, RECORDINGS_DIR)]
    if missing:
        return _fail(EXIT_MISSING_RECORDINGS, _names(missing, "case(s) without a recording"))

    if not BASELINE_PATH.is_file():
        return _fail(EXIT_NO_BASELINE, "no baseline (evals/baseline.json, committed in PR 9)")

    return _fail(EXIT_SCORING_NOT_IMPLEMENTED, "scoring not implemented (PR 7)")


def run_cli() -> NoReturn:
    raise SystemExit(main(sys.argv[1:]))


if __name__ == "__main__":
    run_cli()

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
    21 a case is invalid or incomplete (exactly one source.*, an expected.json)
    22 a case has no recording under RECORDINGS_DIR
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


def _discover(fixtures: Path, include_private: bool) -> list[Case]:
    def dirs(root: Path) -> list[Path]:
        if not root.is_dir():
            return []
        return sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))

    cases = [Case(p.name, p, False) for p in dirs(fixtures) if p.name != PRIVATE_DIRNAME]
    if include_private:
        cases += [Case(p.name, p, True) for p in dirs(fixtures / PRIVATE_DIRNAME)]
    return cases


def _is_valid(case: Case) -> bool:
    if not case.private and not _SAFE_NAME.fullmatch(case.name):
        return False
    sources = [p for p in case.path.glob("source.*") if p.is_file()]
    return len(sources) == 1 and (case.path / "expected.json").is_file()


def _has_recording(case: Case, recordings: Path) -> bool:
    # Placeholder key: PR 2/7 define the real one (prompt + schema + model hash).
    if not recordings.is_dir():
        return False
    return any(p.is_file() and p.name.startswith(case.name) for p in recordings.rglob("*"))


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

    invalid = [c for c in cases if not _is_valid(c)]
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

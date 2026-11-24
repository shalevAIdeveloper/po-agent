"""`uv run python -m evals.generate [--check] [case...]` (from apps/api).

Without `--check`: renders the cases (all, or the named ones) into `evals/fixtures/` and
writes only the files whose content changed. With `--check`: writes nothing, prints each
difference and exits 1 if there is any (also for a fixture directory with no case in the
registry). Exit 2: usage error or an unknown case name.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from evals.generate.cases import by_name
from evals.generate.files import build_files, problems, write
from evals.run import FIXTURES_DIR, PRIVATE_DIRNAME


def _unregistered(root: Path, names: set[str]) -> list[str]:
    if not root.is_dir():
        return []
    return sorted(
        p.name
        for p in root.iterdir()
        if p.is_dir()
        and not p.name.startswith(".")
        and p.name.lower() != PRIVATE_DIRNAME
        and p.name not in names
    )


def main(argv: Sequence[str] | None = None, root: Path = FIXTURES_DIR) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.generate")
    parser.add_argument("--check", action="store_true", help="compare only; exit 1 on a diff")
    parser.add_argument("cases", nargs="*", metavar="case")
    args = parser.parse_args(argv)
    registry = by_name()
    unknown = sorted(set(args.cases) - set(registry))
    if unknown:
        print(f"unknown case(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    chosen = [registry[name] for name in args.cases] if args.cases else list(registry.values())
    found: list[str] = []
    for case in chosen:
        built = build_files(case)
        if args.check:
            found += problems(built, root)
        else:
            for name in write(built, root):
                print(f"wrote {name}")
    if not args.cases:
        found += [f"{name}: no such case" for name in _unregistered(root, set(registry))]
    for line in found:
        print(line, file=sys.stderr)
    # Stale directories are reported, never deleted.
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())

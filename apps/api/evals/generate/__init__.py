"""Synthetic eval case generator (M1 PR 6): `uv run python -m evals.generate [--check] [case...]`.

Every case is one structured spec (`spec.py`, registered in `cases/`). The document
(`source.*`) and its ground truth (`expected.json`) are both rendered from that spec, so
the truth is correct by construction (docs/plans/m1.md §5). All data is fictional
(`data.py`). Rendering is deterministic: no clock, no global random state, fixed PDF/ZIP/
email metadata; `--check` compares a fresh render with the committed files per format.

Dev-only: reportlab is in the dev dependency group, and nothing under `evals/` ships in an
image (`.railwayignore`, `apps/api/.dockerignore`).
"""

from typing import Final

GENERATOR: Final = "evals.generate"
# Bump when a renderer change is meant to change committed files (they are regenerated).
GENERATOR_VERSION: Final = 1

# Extraction evals

Measures extraction accuracy on a fixed set of purchase orders. Plan and metrics:
`docs/plans/m1.md` §5 and §6. ADR 0015 covers the scoring.

## Layout
```
evals/
  run.py            the runner: uv run python -m evals.run ...
  fixtures/<case>/  one directory per case
    source.<ext>    the document: pdf | png | jpg | xlsx | csv | eml (exactly one source.*)
    expected.json   ground truth; validates against extraction schema v1
    meta.json       synthetic: true, generator + seed, tags, input type (checked from PR 6)
  fixtures/private/ real POs, local only: gitignored, never committed
  recordings/       recorded LLM answers that CI replays (from PR 9)
  baseline.json     the committed reference scores (from PR 9)
```
Case names are lowercase letters, digits, `-` and `_` (max 64 characters). The runner
prints case names, so a name must never carry customer data.

**Real customer documents never go into git.** Committed fixtures are synthetic only
(fictional names, `*.example` / `*.test` domains, `555-01xx` phones). Real POs live only in
`fixtures/private/`. That folder is skipped unless `--include-private` is given, and the
runner never prints the names of private cases. Their recordings are never committed either.

## Running
```powershell
cd apps/api
uv run python -m evals.run --replay --fail-on-regression   # what CI runs: no key, no network
uv run python -m evals.run --live                          # your key in $env:LLM_API_KEY (PR 9)
```
Options: exactly one of `--replay` / `--live`; `--fail-on-regression`; `--fixtures DIR`
(default `evals/fixtures`); `--include-private`.

## Exit codes
The runner never passes silently (owner rule, M1 PR 0b). Until scoring (PR 7) and the
baseline (PR 9) exist, **every run exits non-zero**. It writes one line to stderr, with no
document content, key values or private case names. The checks run in this order:

| Code | Meaning |
|---|---|
| 2 | Bad usage, or `--help` (no exit 0 in this PR) |
| 10 | `--live` without a non-empty `LLM_API_KEY` |
| 11 | `--live` with a key: not implemented until PR 9 (no network call is made) |
| 20 | No fixture cases found (what CI sees until PR 6) |
| 21 | A case is invalid: unsafe name, missing `expected.json`, or not exactly one `source.*` |
| 22 | A case has no recording under `recordings/` (for now: a file whose name starts with the case name; PR 2/7 define the real key) |
| 23 | No `baseline.json` |
| 30 | Everything is present, but scoring is not implemented (PR 7) |

From PR 7 on, 0 means a scored replay with no regression against the baseline.

## CI
The `evals` job (`.github/workflows/ci.yml`) runs on pull requests that touch
`app/prompts/**`, `app/extraction/**`, `app/llm/**` or `evals/**`. It uses no secrets: the
live eval is a separate, manual workflow (PR 8). The job stays in `ci-ok`. Only on PRs into
`m1/engine`, and only until `baseline.json` exists, does `ci-ok` accept its failure.

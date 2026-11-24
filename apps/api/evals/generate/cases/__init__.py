"""The registry: every committed case, once. `fixtures/<name>/` must match it exactly."""

from evals.generate.cases import pdf_clean, pdf_special
from evals.generate.spec import Case

CASES: tuple[Case, ...] = (*pdf_clean.CASES, *pdf_special.CASES)


def by_name() -> dict[str, Case]:
    found: dict[str, Case] = {}
    for case in CASES:
        if case.name in found:
            raise ValueError(f"duplicate case name: {case.name}")
        found[case.name] = case
    return found

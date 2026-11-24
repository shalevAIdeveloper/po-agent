"""QA pass on M1 PR 5: extraction paths the T7-T11 tests left unexercised."""

import pytest
import structlog

from app.extraction import spreadsheet as spreadsheet_module
from app.llm.fake import FakeResponse
from app.llm.prompts import load_prompt
from tests.extraction.test_spreadsheet import STANDARD, mapped, run

# extract_po/v1 as rewritten in place by M1-PRICE-NOTATION (owner, 2026-11-24; Q15 allows
# it: no extraction stored and no eval baseline yet). Previous hashes:
# - d0419a6e4fb4511b1d6e778d5d211a995973a3b527a7a3847535c10171935c17 (PR 3, 63bc609);
# - 5913a350009f7e31fb97ac87601d059fbc1a972492e30afd1cee37a5c9fae231 (1f3d276, before the
#   `sec` L fix to rule 14's "Per" column).
# Refactors of the call path or of `parse_prompt` may not change it; the replay cassettes
# (PR 7) and every stored extraction records it. From the PR 9 baseline on, v1 is immutable.
EXTRACT_PO_V1_SHA256 = "b46c228e6b38848346fcbcba6a74eb728ad78b33379e48a9e0a3d5af52586f6d"


def test_the_extract_po_prompt_hash_is_pinned() -> None:
    assert load_prompt("extract_po", 1).sha256 == EXTRACT_PO_V1_SHA256


async def test_a_grounding_crash_keeps_the_spreadsheet_extraction_and_its_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # As for `extract_po` (`sec` M1): a bug in the flag-only self-check must not lose
    # the billed mapping call or the extraction built from the cells.
    def boom(*_args: object) -> None:
        raise ArithmeticError("boom 00123-A")

    monkeypatch.setattr(spreadsheet_module, "check_grounding", boom)
    with structlog.testing.capture_logs() as logs:
        result = await run(STANDARD, FakeResponse(mapped()))

    assert result.status == "extracted"
    assert result.extraction is not None
    assert len(result.extraction.lines) == 2
    assert result.grounding_checked is False
    # The footer total is listed as a skipped row (owner, M4); nothing else.
    assert [w.code for w in result.warnings] == ["rows_skipped"]
    assert [(c.purpose, c.attempt) for c in result.calls] == [("map_columns", 1)]
    [failed] = [line for line in logs if line["event"] == "extraction.grounding_failed"]
    assert failed["error_type"] == "ArithmeticError"
    assert "00123-A" not in repr(logs)  # neither the cell nor the exception message

"""QA pass on M1 PR 5: extraction paths the T7-T11 tests left unexercised."""

import pytest
import structlog

from app.extraction import spreadsheet as spreadsheet_module
from app.llm.fake import FakeResponse
from app.llm.prompts import load_prompt
from tests.extraction.test_spreadsheet import STANDARD, mapped, run

# extract_po/v1 as merged in PR 3 (63bc609, before PR 5). PR 5 refactored the call path
# (T8) and tightened `parse_prompt` (M1-BRACES); neither may change this prompt's hash,
# which the replay cassettes (PR 7) and every stored extraction record.
EXTRACT_PO_V1_SHA256 = "d0419a6e4fb4511b1d6e778d5d211a995973a3b527a7a3847535c10171935c17"


def test_the_extract_po_prompt_hash_is_the_pr3_one() -> None:
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
    assert result.warnings == ()
    assert [(c.purpose, c.attempt) for c in result.calls] == [("map_columns", 1)]
    [failed] = [line for line in logs if line["event"] == "extraction.grounding_failed"]
    assert failed["error_type"] == "ArithmeticError"
    assert "00123-A" not in repr(logs)  # neither the cell nor the exception message

from dataclasses import replace

import pytest

from app.llm.types import ImagePart, TextPart
from tests.llm.helpers import PO_TEXT, Answer, make_request


def test_fingerprint_is_stable() -> None:
    assert make_request().fingerprint() == make_request().fingerprint()
    assert len(make_request().fingerprint()) == 64


@pytest.mark.parametrize(
    "change",
    [
        {"prompt_version": "extract_po/v2"},
        {"schema_version": "answer.v2"},
        {"model": "claude-haiku-5-5"},
        {"effort": "high"},
        {"max_output_tokens": 2000},
        {"system": "another prompt"},
        {"content": (TextPart(PO_TEXT + " "),)},
        {"content": (ImagePart(data=b"a", media_type="image/png"),)},
    ],
)
def test_everything_that_decides_the_output_changes_the_fingerprint(
    change: dict[str, object],
) -> None:
    base = make_request()
    assert replace(base, **change).fingerprint() != base.fingerprint()  # type: ignore[arg-type]


def test_schema_edit_without_a_version_bump_changes_the_fingerprint() -> None:
    # `sec` L5: replay must never answer for an edited schema under the same label.
    class AnswerEdited(Answer):
        note: str | None = None

    base = make_request()
    assert replace(base, output_model=AnswerEdited).fingerprint() != base.fingerprint()


def test_image_bytes_change_the_fingerprint() -> None:
    a = make_request(content=(ImagePart(data=b"one", media_type="image/png"),))
    b = make_request(content=(ImagePart(data=b"two", media_type="image/png"),))
    assert a.fingerprint() != b.fingerprint()


def test_repr_does_not_include_prompt_content_or_image_bytes() -> None:
    request = make_request(content=(TextPart(PO_TEXT), ImagePart(b"IMGBYTES", "image/png")))
    assert PO_TEXT not in repr(request)
    assert "IMGBYTES" not in repr(request)
    assert "SYSTEM-PROMPT-MARKER" not in repr(request)

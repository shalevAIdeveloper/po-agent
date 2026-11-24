"""EML cases (M1 PR 6 T6): From is meta.sender_email, fixed headers, CRLF, no clock."""

import pytest

from app.extraction.inputs import ParsedDocument, run_parsers
from evals.generate import meta, render_email
from evals.generate.cases import CASES
from evals.generate.files import META_NAME
from evals.generate.spec import Case, EmailDoc
from evals.run import FIXTURES_DIR

EMAILS = [pytest.param(c, id=c.name) for c in CASES if isinstance(c.document, EmailDoc)]


def _source(case: Case) -> bytes:
    return (FIXTURES_DIR / case.name / case.source_name).read_bytes()


def test_there_are_six_emails() -> None:
    assert len(EMAILS) == 6


@pytest.mark.parametrize("case", EMAILS)
def test_from_and_date_are_the_meta_values(case: Case) -> None:
    parsed = run_parsers(_source(case))
    data = meta.load((FIXTURES_DIR / case.name / META_NAME).read_bytes())

    assert isinstance(parsed, ParsedDocument)
    assert parsed.claimed_from == data.sender_email
    assert parsed.claimed_date == data.received_at


@pytest.mark.parametrize("case", EMAILS)
def test_every_line_ends_with_crlf_and_headers_are_fixed(case: Case) -> None:
    data = _source(case)
    assert data.count(b"\n") == data.count(b"\r\n")
    assert render_email.message_id(case, 1).encode() in data or b"In-Reply-To" in data
    if b"multipart" in data:
        assert f'boundary="=_eval_{case.name}"'.encode() in data


@pytest.mark.parametrize("case", EMAILS)
def test_an_email_renders_the_same_twice(case: Case) -> None:
    assert isinstance(case.document, EmailDoc)
    assert render_email.render(case, case.document) == render_email.render(case, case.document)


def test_the_reply_points_at_the_quoted_message() -> None:
    case = next(c for c in CASES if c.name == "eml_reply_with_quoted_old_order")
    data = _source(case)
    assert f"In-Reply-To: {render_email.message_id(case, 1)}".encode() in data
    assert f"Message-ID: {render_email.message_id(case, 2)}".encode() in data


def test_an_email_without_a_body_is_an_error() -> None:
    case = next(c for c in CASES if c.name == "eml_free_text")
    with pytest.raises(ValueError, match="needs a body"):
        render_email.render(case, EmailDoc("s", "n", "orders@example.com", None))

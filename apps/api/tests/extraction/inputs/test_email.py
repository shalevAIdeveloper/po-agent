from datetime import UTC, datetime, timedelta, timezone
from email.message import EmailMessage

import pytest

from app.extraction.inputs import parse_document
from app.extraction.inputs.email import html_to_text, parse_email
from app.extraction.inputs.errors import InputError
from tests.extraction.inputs.files import make_email, make_pdf

HTML = """<html><head><title>T-TITLE</title><style>p {color: red}</style>
<script>alert("S-SCRIPT")</script></head>
<body><p>Please   ship
   PO&nbsp;4500012345 &amp; confirm.</p>
<table><tr><th>Item</th><th>Qty</th></tr><tr><td>00123-A</td><td>12</td></tr></table>
<img src="https://tracker.example/pixel.png"><br>Thanks</body></html>"""


def test_plain_body_is_the_text() -> None:
    parsed = parse_email(make_email())
    assert parsed.text == "PO 4500012345\n1 x Widget 00123-A"
    assert parsed.warnings == ()


def test_plain_is_preferred_over_html() -> None:
    parsed = parse_email(make_email(plain="PLAIN BODY", html="<p>HTML BODY</p>"))
    assert parsed.text == "PLAIN BODY"


def test_html_only_body_becomes_text() -> None:
    text = parse_email(make_email(plain=None, html=HTML)).text
    assert "S-SCRIPT" not in text
    assert "color: red" not in text
    assert "T-TITLE" not in text
    assert "tracker.example" not in text
    assert "Please ship PO\xa04500012345 & confirm." in text
    assert text.split("\n") == [
        "Please ship PO\xa04500012345 & confirm.",
        "Item\tQty",
        "00123-A\t12",
        "Thanks",
    ]


def test_html_tables_keep_cells_apart() -> None:
    text = html_to_text("<table><tr><td>A1</td><td>12</td></tr><tr><td>B2</td></tr></table>")
    assert text.split("\n") == ["A1\t12", "B2"]


def test_attachments_are_ignored_with_a_warning() -> None:
    parsed = parse_email(make_email(attachment=make_pdf(["PO 999"])))
    assert parsed.text == "PO 4500012345\n1 x Widget 00123-A"
    assert parsed.warnings == ("attachments_ignored",)
    assert "po-secret-name" not in repr(parsed)


def test_a_forwarded_message_is_an_ignored_attachment() -> None:
    inner = EmailMessage()
    inner["From"] = "other@acme.example"
    inner.set_content("FORWARDED-BODY PO 777")
    parsed = parse_email(make_email(plain="see below", forwarded=inner))
    assert parsed.text == "see below"
    assert parsed.warnings == ("attachments_ignored",)


def test_from_and_date_are_kept_for_display() -> None:
    parsed = parse_email(make_email())
    assert parsed.display_from == "buyer@acme.example"
    assert parsed.display_date == datetime(
        2026, 11, 16, 9, 30, tzinfo=timezone(timedelta(hours=-5))
    )
    assert parsed.display_date.astimezone(UTC).hour == 14


@pytest.mark.parametrize("sender", ["not an address", "", "<>", "a@" + "b" * 260 + ".example"])
def test_an_unusable_from_is_none(sender: str) -> None:
    assert parse_email(make_email(sender=sender)).display_from is None


@pytest.mark.parametrize("date", [None, "yesterday", "Mon, 16 Nov 2026 09:30:00 -0000"])
def test_a_missing_bad_or_zoneless_date_is_none(date: str | None) -> None:
    # RFC 5322: "-0000" means the zone is unknown.
    assert parse_email(make_email(date=date)).display_date is None


def test_an_unknown_charset_does_not_fail() -> None:
    data = (
        b"From: a@acme.example\nSubject: PO\nMIME-Version: 1.0\n"
        b"Content-Type: text/plain; charset=x-no-such-charset\n\nPO 42 \xff\xfe ok\n"
    )
    text = parse_email(data).text
    assert text.startswith("PO 42 ")
    assert text.endswith(" ok")


def test_an_email_without_a_body_is_empty() -> None:
    with pytest.raises(InputError, match="empty_document"):
        parse_email(make_email(plain="  \n "))


def test_parse_document_maps_display_fields_to_the_prompt_metadata() -> None:
    document = parse_document(make_email())
    assert document.kind == "eml"
    assert document.images == ()
    assert "buyer@acme.example" not in repr(document)
    model_input = document.to_document_input()
    assert model_input.sender_email == "buyer@acme.example"
    assert model_input.received_at == document.display_date
    assert model_input.text == document.text

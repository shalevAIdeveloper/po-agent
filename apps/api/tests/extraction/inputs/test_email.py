from datetime import UTC, datetime, timedelta, timezone
from email.message import EmailMessage

import pytest

from app.extraction.inputs import email as email_module
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


def test_from_and_date_are_kept_as_claims() -> None:
    parsed = parse_email(make_email())
    assert parsed.claimed_from == "buyer@acme.example"
    assert parsed.claimed_date == datetime(
        2026, 11, 16, 9, 30, tzinfo=timezone(timedelta(hours=-5))
    )
    assert parsed.claimed_date.astimezone(UTC).hour == 14


@pytest.mark.parametrize("sender", ["not an address", "", "<>", "a@" + "b" * 260 + ".example"])
def test_an_unusable_from_is_none(sender: str) -> None:
    assert parse_email(make_email(sender=sender)).claimed_from is None


@pytest.mark.parametrize(
    "header",
    [
        b'From: "\x07bell"@acme.example',  # control character (`sec` L1)
        b"From: a@tr\xc3\xbcsted.example",  # raw 8-bit: lone surrogates after parsing
        b"From: a@acme.example\xe2\x80\x8b",  # zero-width space (format character)
    ],
)
def test_a_from_with_unprintable_characters_is_none(header: bytes) -> None:
    data = header + b"\nSubject: PO\nDate: Mon, 16 Nov 2026 09:30:00 -0500\n\nPO 1\n"
    assert parse_email(data).claimed_from is None


@pytest.mark.parametrize(
    "date",
    [
        None,
        "yesterday",
        "Mon, 16 Nov 2026 09:30:00 -0000",  # RFC 5322: "-0000" means the zone is unknown
        "Fri, 31 Dec 9999 23:59:00 -2359",  # `sec` L1: astimezone(UTC) overflowed
        "Mon, 1 Jan 1985 09:30:00 +0000",
        "Mon, 1 Jan 2099 09:30:00 +0000",
    ],
)
def test_a_missing_bad_zoneless_or_implausible_date_is_none(date: str | None) -> None:
    assert parse_email(make_email(date=date)).claimed_date is None


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


def test_a_forwarded_message_alone_is_empty() -> None:
    inner = EmailMessage()
    inner.set_content("FORWARDED-BODY PO 777")
    outer = EmailMessage()
    outer["From"] = "a@acme.example"
    outer.add_attachment(inner)
    with pytest.raises(InputError, match="empty_document"):
        parse_email(bytes(outer))


def test_html_without_a_closing_head_keeps_its_body() -> None:
    # `qa`: HTML5 lets `</head>` be left out; the whole body used to be dropped.
    assert html_to_text("<head><title>T</title><body><p>PO 123</p>") == "PO 123"
    html = "<html><head><meta charset=utf-8><body><p>PO 4500012345 qty 12</p></body></html>"
    assert parse_email(make_email(plain=None, html=html)).text == "PO 4500012345 qty 12"


def test_a_blank_plain_part_falls_back_to_html() -> None:
    parsed = parse_email(make_email(plain=" \n", html="<p>PO 4500012345 qty 12</p>"))
    assert parsed.text == "PO 4500012345 qty 12"


def test_plain_and_html_with_different_numbers_are_flagged() -> None:
    # `sec` M3: the model reads the plain part, a mail client shows the HTML part.
    parsed = parse_email(make_email(plain="PO 4500 qty 12", html="<p>PO 4500 qty 120</p>"))
    assert parsed.text == "PO 4500 qty 12"
    assert parsed.warnings == ("alternative_parts_differ",)


def test_plain_and_html_with_the_same_numbers_are_not_flagged() -> None:
    parsed = parse_email(
        make_email(
            plain="PO 4500012345\nQty 1,200 at 3.50",
            html="<p>PO <b>4500012345</b></p><table><tr><td>Qty</td><td>1200</td>"
            "<td>at 3.50</td></tr></table><a href='https://x.example/9'>site</a>",
        )
    )
    assert parsed.warnings == ()


@pytest.mark.parametrize(
    "hidden",
    [
        '<div style="display: none">qty 999</div>',
        "<span style='VISIBILITY:hidden'>qty 999</span>",
        '<span style="font-size:0px">qty 999</span>',
        '<span style="color:red;font-size:0">qty 999</span>',
        "<div hidden>qty 999</div>",
    ],
)
def test_hidden_html_text_is_flagged_when_the_html_is_read(hidden: str) -> None:
    parsed = parse_email(make_email(plain=None, html=f"<p>PO 4500 qty 12</p>{hidden}"))
    assert "hidden_html_text" in parsed.warnings


@pytest.mark.parametrize(
    "style", ["font-size:0.5em", "font-size:10px", "display:block", "visibility:visible"]
)
def test_visible_styles_are_not_flagged(style: str) -> None:
    parsed = parse_email(make_email(plain=None, html=f'<p style="{style}">PO 4500 qty 12</p>'))
    assert parsed.warnings == ()


def test_hidden_markup_inside_head_is_not_flagged() -> None:
    html = "<head><style>.x{display:none}</style></head><body><p>PO 4500</p></body>"
    assert parse_email(make_email(plain=None, html=html)).warnings == ()


def test_html_over_the_cap_is_refused_before_conversion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(email_module, "MAX_HTML_CHARS", 50)
    with pytest.raises(InputError, match="document_too_large"):
        parse_email(make_email(plain=None, html="<p>" + "x" * 60 + "</p>"))


def test_parse_document_maps_the_claims_to_the_prompt_metadata() -> None:
    document = parse_document(make_email())
    assert document.kind == "eml"
    assert document.images == ()
    assert "buyer@acme.example" not in repr(document)
    model_input = document.to_document_input()
    assert model_input.sender_email == "buyer@acme.example"
    assert model_input.received_at == document.claimed_date
    assert model_input.text == document.text

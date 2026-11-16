import pytest

from app.extraction.inputs.detect import PDF_HEADER_WINDOW, detect_kind
from app.extraction.inputs.errors import InputError
from tests.extraction.inputs.files import make_email, make_image, make_pdf


def kind_or_code(data: bytes) -> str:
    try:
        return detect_kind(data)
    except InputError as error:
        return error.code


def test_the_bytes_decide_not_a_name() -> None:
    # There is no filename parameter at all: a PNG named "po.pdf" is a PNG.
    assert detect_kind(make_image()) == "png"
    assert detect_kind(make_image(fmt="JPEG")) == "jpeg"
    assert detect_kind(make_pdf(["PO 1"])) == "pdf"
    assert detect_kind(make_email()) == "eml"


def test_a_pdf_header_after_a_few_junk_bytes_is_a_pdf() -> None:
    assert detect_kind(b"\r\n\x00junk" + make_pdf(["PO 1"])) == "pdf"
    late = b"x" * PDF_HEADER_WINDOW + make_pdf(["PO 1"])
    assert kind_or_code(late) == "unsupported_type"


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic",  # HEIC (M6)
        b"GIF89a\x01\x00\x01\x00",
        b"RIFF\x10\x00\x00\x00WEBPVP8 ",
        b"PK\x03\x04\x14\x00\x06\x00",  # XLSX (PR 5)
        b"Item,Qty,Price\n00123-A,12,1.50\n",  # CSV (PR 5)
        b"Item: 00123-A\nQty: 12\n\nthanks",  # header-like, but not an email
        b"Subject: hi\nTo: a@b.example\n\nbody",  # no From
        b"From: a@b.example\nSubject: hi\nno blank line, so no body",
        b"From: a@b.example\nnot a header line\n\nbody",
        b"just some text",
    ],
)
def test_anything_else_is_unsupported(data: bytes) -> None:
    assert kind_or_code(data) == "unsupported_type"


def test_an_email_with_folded_headers_and_crlf_is_an_email() -> None:
    data = (
        b"Received: from mx.example\r\n\tby mx2.example; Mon, 16 Nov 2026\r\n"
        b"From: Buyer <buyer@acme.example>\r\nSubject: PO\r\n\r\nbody\r\n"
    )
    assert detect_kind(data) == "eml"

import json
import pickle
import struct
from datetime import UTC, datetime
from typing import Any

import pytest

from app.extraction.inputs import ParsedDocument, Rejection, run_parsers
from app.extraction.inputs.limits import MAX_PDF_PAGES, MAX_TEXT_CHARS, MAX_TOTAL_IMAGE_BYTES
from app.extraction.inputs.wire import MAX_REPLY_BYTES, WireError, decode, encode
from tests.extraction.inputs.files import make_email, make_image, make_pdf


def frame(header: dict[str, Any] | str, body: bytes = b"") -> bytes:
    raw = (header if isinstance(header, str) else json.dumps(header)).encode()
    return struct.pack(">Q", len(raw)) + raw + body


def ok_header(**changes: Any) -> dict[str, Any]:
    header: dict[str, Any] = {
        "v": 1,
        "status": "ok",
        "kind": "png",
        "text": None,
        "page_count": 1,
        "warnings": [],
        "claimed_from": None,
        "claimed_date": None,
        "images": [{"media_type": "image/png", "length": 3}],
    }
    header.update(changes)
    return header


@pytest.mark.parametrize(
    "data", [make_pdf(["PO 4500012345 00123-A"]), make_image(), make_email(), b"GIF89a"]
)
def test_round_trip(data: bytes) -> None:
    outcome = run_parsers(data)
    assert decode(encode(outcome)) == outcome


def test_round_trip_keeps_the_claimed_date_and_zone() -> None:
    document = ParsedDocument(
        kind="eml",
        text="t",
        images=(),
        page_count=1,
        warnings=("attachments_ignored",),
        claimed_from="a@b.example",
        claimed_date=datetime(2026, 11, 16, 14, 30, tzinfo=UTC),
    )
    assert decode(encode(document)) == document


def test_a_rejection_round_trips() -> None:
    rejection = Rejection(code="invalid_file", kind="pdf", error_type="PdfiumError")
    assert decode(encode(rejection)) == rejection


@pytest.mark.parametrize(
    "reply",
    [
        b"",
        b"\0" * 7,
        pickle.dumps(Rejection(code="invalid_file")),  # never unpickled
        frame("[]"),
        frame("not json"),
        frame('{"v": 1, "status": "ok", "page_count": NaN}'),
        frame(ok_header(v=2), b"abc"),
        frame(ok_header(status="maybe"), b"abc"),
        frame(ok_header(extra=1), b"abc"),
        frame(ok_header(kind="exe"), b"abc"),
        frame(ok_header(page_count=0), b"abc"),
        frame(ok_header(page_count=MAX_PDF_PAGES + 1), b"abc"),
        frame(ok_header(page_count=True), b"abc"),
        frame(ok_header(warnings=["run_this"]), b"abc"),
        frame(ok_header(warnings=["attachments_ignored"] * 2), b"abc"),
        frame(ok_header(text=7), b"abc"),
        frame(ok_header(text="x" * (MAX_TEXT_CHARS + 1)), b"abc"),
        frame(ok_header(claimed_from="x" * 255), b"abc"),
        frame(ok_header(claimed_date="2026-11-16T09:30:00"), b"abc"),  # no zone
        frame(ok_header(claimed_date="yesterday"), b"abc"),
        frame(ok_header(images=[{"media_type": "image/svg+xml", "length": 3}]), b"abc"),
        frame(ok_header(images=[{"media_type": "image/png", "length": 0}]), b""),
        frame(ok_header(images=[{"media_type": "image/png", "length": 4}]), b"abc"),
        frame(ok_header(), b"abcd"),  # trailing bytes
        frame(ok_header(images=[]), b""),  # neither text nor images
        frame(ok_header(images=[{"media_type": "image/png", "length": 3, "x": 1}]), b"abc"),
        frame({"v": 1, "status": "rejected", "code": "rm -rf", "kind": None, "error_type": None}),
        frame(
            {
                "v": 1,
                "status": "rejected",
                "code": "invalid_file",
                "kind": None,
                "error_type": "Bad Type\n",
            }
        ),
        frame(
            {
                "v": 1,
                "status": "rejected",
                "code": "invalid_file",
                "kind": None,
                "error_type": None,
            },
            b"x",
        ),
        struct.pack(">Q", 10**9) + b"{}",
        struct.pack(">Q", 3) + b"{}",
    ],
)
def test_a_malformed_reply_is_refused(reply: bytes) -> None:
    with pytest.raises(WireError):
        decode(reply)


@pytest.mark.parametrize(
    "header",
    [
        # `sec` delta L-1: unhashable values where a literal belongs are WireError.
        ok_header(kind=["png"]),
        ok_header(warnings=[["attachments_ignored"]]),
        ok_header(images=[{"media_type": ["image/png"], "length": 3}]),
        # `sec` delta L-2: the parent re-applies the parsers' sanitizing.
        ok_header(text="PO" + chr(0) + "4500"),
        ok_header(text=chr(0xD800)),  # a lone surrogate, sent as a JSON escape
        ok_header(text=""),
        ok_header(claimed_from="no-at-sign.example"),
        ok_header(claimed_from="@b.example"),
        ok_header(claimed_from="a" + chr(0x202E) + "@b.example"),
        ok_header(claimed_from="a@b.example\n"),
        ok_header(claimed_date="1980-01-01T00:00:00+00:00"),
        ok_header(claimed_date="2999-01-01T00:00:00+00:00"),
    ],
)
def test_a_reply_a_parser_could_not_produce_is_refused(header: dict[str, Any]) -> None:
    with pytest.raises(WireError):
        decode(frame(header, b"abc"))


def test_a_rejection_with_an_unhashable_code_is_refused() -> None:
    header = {"v": 1, "status": "rejected", "code": {}, "kind": None, "error_type": None}
    with pytest.raises(WireError):
        decode(frame(header))


def test_image_bytes_over_the_total_cap_are_refused() -> None:
    # `sec` delta L-3.
    length = MAX_TOTAL_IMAGE_BYTES + 1
    header = ok_header(images=[{"media_type": "image/png", "length": length}])
    with pytest.raises(WireError):
        decode(frame(header, b"\0" * length))


@pytest.mark.parametrize("char", [chr(0x1F4E6), chr(1), '"', "\\"])
def test_the_longest_text_fits_whatever_its_characters(char: str) -> None:
    # `sec` delta L-4: 4-byte characters and escaped ones at MAX_TEXT_CHARS.
    document = ParsedDocument(
        kind="eml", text=char * MAX_TEXT_CHARS, images=(), page_count=1, warnings=()
    )
    assert decode(encode(document)) == document


def test_a_reply_over_the_read_limit_is_refused() -> None:
    with pytest.raises(WireError):
        decode(frame(ok_header(), b"abc").ljust(MAX_REPLY_BYTES + 1, b"\0"))

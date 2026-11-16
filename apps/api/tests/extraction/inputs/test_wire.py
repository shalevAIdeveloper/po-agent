import json
import pickle
import struct
from datetime import UTC, datetime
from typing import Any

import pytest

from app.extraction.inputs import ParsedDocument, Rejection, run_parsers
from app.extraction.inputs.limits import MAX_PDF_PAGES, MAX_TEXT_CHARS
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


def test_a_reply_over_the_read_limit_is_refused() -> None:
    with pytest.raises(WireError):
        decode(frame(ok_header(), b"abc").ljust(MAX_REPLY_BYTES + 1, b"\0"))

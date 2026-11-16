import contextlib

import pytest
import structlog

import app.extraction.inputs as inputs
from app.extraction.inputs import ParsedDocument, Rejection, parse_document, run_parsers
from app.extraction.inputs.detect import DocumentKind
from app.extraction.inputs.errors import InputError
from app.extraction.inputs.limits import MAX_FILE_BYTES, MAX_TEXT_CHARS, MAX_TOTAL_IMAGE_BYTES
from tests.extraction.inputs.files import make_email, make_image, make_pdf

SECRETS = ("SECRET-BODY-5e1a", "victim@acme.example", "po-secret-name")


def test_a_file_over_the_size_cap_is_refused_before_detection() -> None:
    data = make_pdf(["PO 1"]).ljust(MAX_FILE_BYTES + 1, b"\0")
    assert run_parsers(data) == Rejection(code="file_too_large")


def test_exactly_the_size_cap_is_allowed_through() -> None:
    data = make_image().ljust(MAX_FILE_BYTES, b"\0")  # trailing bytes after IEND are ignored
    assert isinstance(run_parsers(data), ParsedDocument)


def test_too_much_text_is_refused() -> None:
    assert run_parsers(make_email(plain="x" * (MAX_TEXT_CHARS + 1))) == Rejection(
        code="document_too_large", kind="eml"
    )
    assert isinstance(run_parsers(make_email(plain="x" * MAX_TEXT_CHARS)), ParsedDocument)


@pytest.mark.parametrize(
    ("text", "cleaned"),
    [
        (None, None),
        ("PO 4500", "PO 4500"),
        ("PO" + chr(0) + " 4500", "PO 4500"),
        ("PO " + chr(0xD800) + "4500", "PO ?4500"),
        (chr(0) * 3, None),
    ],
)
def test_text_is_cleaned_for_storage(text: str | None, cleaned: str | None) -> None:
    # `sec` delta L-2: no NUL, valid UTF-8; the parent checks the same.
    assert inputs.clean_text(text) == cleaned


def test_a_nul_in_the_document_never_reaches_the_text() -> None:
    document = run_parsers(make_email(plain="PO 4500" + chr(0) + "012345"))
    assert isinstance(document, ParsedDocument)
    assert document.text == "PO 4500012345"


def test_too_many_image_bytes_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inputs, "MAX_TOTAL_IMAGE_BYTES", 10)
    assert run_parsers(make_image()) == Rejection(code="document_too_large", kind="png")


def test_the_image_byte_cap_fits_one_request_after_base64() -> None:
    # `sec` L2: base64 adds a third; plus the text, one request must stay under 32 MB.
    assert MAX_TOTAL_IMAGE_BYTES * 4 / 3 + MAX_TEXT_CHARS * 4 < 32_000_000


def test_an_unexpected_parser_error_is_invalid_file_with_its_type_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_kind: DocumentKind, _data: bytes) -> ParsedDocument:
        raise ValueError(SECRETS[0])

    monkeypatch.setattr(inputs, "_parse", boom)
    with structlog.testing.capture_logs() as logs, pytest.raises(InputError) as caught:
        parse_document(make_image())
    assert str(caught.value) == "invalid_file"
    assert caught.value.__cause__ is None
    assert logs == [
        {
            "event": "document.parse_rejected",
            "log_level": "info",
            "kind": "png",
            "size_bytes": len(make_image()),
            "code": "invalid_file",
            "error_type": "ValueError",
        }
    ]


def test_running_out_of_memory_is_parse_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    def oom(_kind: DocumentKind, _data: bytes) -> ParsedDocument:
        raise MemoryError

    monkeypatch.setattr(inputs, "_parse", oom)
    assert run_parsers(make_image()) == Rejection(
        code="parse_failed", kind="png", error_type="MemoryError"
    )


@pytest.mark.parametrize(
    "data",
    [
        make_email(
            plain=f"{SECRETS[0]} PO 1",
            sender=f"Victim <{SECRETS[1]}>",
            attachment=b"%PDF-1.4",
        ),
        make_pdf([f"{SECRETS[0]} {SECRETS[1]}"]),
        make_email(plain=" ", sender=SECRETS[1]),  # rejected: empty_document
    ],
)
def test_logs_carry_no_content(data: bytes) -> None:
    with structlog.testing.capture_logs() as logs, contextlib.suppress(InputError):
        parse_document(data)
    assert len(logs) == 1
    assert logs[0]["event"] in ("document.parsed", "document.parse_rejected")
    for secret in SECRETS:
        assert secret not in repr(logs)


def test_the_parsed_log_line_has_counts() -> None:
    with structlog.testing.capture_logs() as logs:
        document = parse_document(make_email(attachment=b"%PDF-1.4"))
    (entry,) = logs
    assert entry["event"] == "document.parsed"
    assert entry["kind"] == "eml"
    assert entry["warnings"] == ["attachments_ignored"]
    assert entry["text_chars"] == len(document.text or "")
    assert entry["image_count"] == 0


def test_the_repr_of_a_document_shows_no_content() -> None:
    document = parse_document(make_email(plain=SECRETS[0], sender=SECRETS[1]))
    assert SECRETS[0] not in repr(document)
    assert SECRETS[1] not in repr(document)

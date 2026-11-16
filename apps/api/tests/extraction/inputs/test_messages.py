import inspect
from typing import get_args

from app.extraction.inputs.errors import InputErrorCode
from app.extraction.inputs.limits import MAX_FILE_BYTES, MAX_PDF_PAGES, MAX_TABLE_ROWS
from app.extraction.inputs.messages import MESSAGES, user_message


def test_every_code_has_a_message_and_nothing_else_does() -> None:
    assert set(MESSAGES) == set(get_args(InputErrorCode))
    for code in get_args(InputErrorCode):
        assert user_message(code).strip()


def test_messages_are_fixed_text() -> None:
    # The lookup takes only a code: there is no way to pass document data in.
    assert list(inspect.signature(user_message).parameters) == ["code"]
    for message in MESSAGES.values():
        assert "{" not in message and "%" not in message
        assert message.isascii() and message.endswith(".")
        assert message[0].isupper()


def test_the_owners_spreadsheet_messages() -> None:
    assert user_message("unsupported_spreadsheet") == (
        "This spreadsheet format isn't supported. Save it as .xlsx or CSV and upload again."
    )
    assert user_message("encrypted_spreadsheet") == (
        "This workbook is password-protected. Remove the password, save it as .xlsx or CSV, "
        "and upload again."
    )


def test_numbers_in_messages_match_the_limits() -> None:
    assert f"{MAX_FILE_BYTES // (1024 * 1024)} MB" in user_message("file_too_large")
    assert f"{MAX_PDF_PAGES} pages" in user_message("too_many_pages")
    assert f"{MAX_TABLE_ROWS:,} rows" in user_message("too_many_rows")

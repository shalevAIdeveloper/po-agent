"""The sentence a user sees for each input error code (owner, 2026-11-16).

Fixed text per code: a message never carries anything from the file (no name, no value).
PR 13 returns it with the 415/422 response and PR 15 shows it.
"""

from types import MappingProxyType
from typing import Final

from app.extraction.inputs.errors import InputErrorCode

MESSAGES: Final[MappingProxyType[InputErrorCode, str]] = MappingProxyType(
    {
        "unsupported_type": (
            "This file type isn't supported. Upload a PDF, a PNG or JPEG image, an Excel "
            ".xlsx file, a CSV file or an .eml email."
        ),
        "file_too_large": "This file is larger than 20 MB. Upload a smaller file.",
        "too_many_pages": "This PDF has more than 20 pages. Split it and upload the parts.",
        "unsupported_pdf": (
            "This PDF is password-protected. Remove the password and upload it again."
        ),
        "invalid_file": "This file is damaged or isn't a valid file of its type.",
        "image_too_large": "This image is too large to read. Upload a smaller image.",
        "empty_document": "This file has nothing to read.",
        "document_too_large": (
            "This document has more content than can be read at once. Split it and upload "
            "the parts."
        ),
        "too_many_rows": (
            "This spreadsheet has more than 2,000 rows. Split it and upload the parts."
        ),
        "unsupported_spreadsheet": (
            "This spreadsheet format isn't supported. Save it as .xlsx or CSV and upload again."
        ),
        "encrypted_spreadsheet": (
            "This workbook is password-protected. Remove the password, save it as .xlsx or "
            "CSV, and upload again."
        ),
        "parse_timeout": "Reading this file took too long. Try again, or upload another format.",
        "parse_failed": (
            "Something went wrong while reading this file. Try again; if it keeps failing, "
            "contact support."
        ),
    }
)


def user_message(code: InputErrorCode) -> str:
    return MESSAGES[code]

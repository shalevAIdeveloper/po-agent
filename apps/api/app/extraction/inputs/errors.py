"""Stable codes for files we can't use, and warnings for files we used only in part.

Codes are what the document's `error_code` will hold (PR 14): never a parser message, which
can quote the file.
"""

from typing import Literal

InputErrorCode = Literal[
    "unsupported_type",  # not a PDF, PNG, JPEG or EML by its bytes (HEIC, GIF, ZIP, ...)
    "file_too_large",  # over MAX_FILE_BYTES
    "too_many_pages",  # PDF over MAX_PDF_PAGES (Q12)
    "unsupported_pdf",  # PDF that needs a password to open
    "invalid_file",  # the bytes claim a type but don't parse as it
    "image_too_large",  # image over MAX_SOURCE_PIXELS (decompression bomb guard)
    "empty_document",  # nothing to read: no pages, or an email without a body
    "document_too_large",  # text or rendered images over what one LLM request can carry
    "parse_timeout",  # the parsing child process ran out of time (isolate.py)
    "parse_failed",  # the parsing child process died (native crash, memory limit)
]

InputWarningCode = Literal[
    "attachments_ignored",  # EML attachments / forwarded messages were not read
    # EML: the plain-text and HTML versions carry different numbers. The model read the
    # plain text; a mail client shows the HTML (`sec` M3).
    "alternative_parts_differ",
    # EML HTML: text hidden by inline style (display:none, visibility:hidden, font-size:0)
    # is in what the model read but not on screen in a mail client (`sec` M3).
    "hidden_html_text",
]


class InputError(Exception):
    """A file we can't turn into a document. `str(error)` is the code, nothing else."""

    def __init__(self, code: InputErrorCode) -> None:
        super().__init__(code)
        self.code: InputErrorCode = code

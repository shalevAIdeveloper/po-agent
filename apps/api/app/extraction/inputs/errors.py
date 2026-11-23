"""Stable codes for files we can't use, and warnings for files we used only in part.

Codes are what the document's `error_code` will hold (PR 14): never a parser message, which
can quote the file. `messages.py` maps every code to the sentence the user sees.
"""

from typing import Literal

InputErrorCode = Literal[
    "unsupported_type",  # not a PDF, PNG, JPEG, EML, XLSX or CSV by its bytes (HEIC, GIF, ...)
    "file_too_large",  # over MAX_FILE_BYTES
    "too_many_pages",  # PDF over MAX_PDF_PAGES (Q12)
    "unsupported_pdf",  # PDF that needs a password to open
    "invalid_file",  # the bytes claim a type but don't parse as it
    "image_too_large",  # image over MAX_SOURCE_PIXELS (decompression bomb guard)
    "empty_document",  # nothing to read: no pages, or an email without a body
    "document_too_large",  # text or rendered images over what one LLM request can carry
    "too_many_rows",  # spreadsheet over MAX_TABLE_ROWS / MAX_ROWS_READ (Q12)
    # `.xls`, `.xlsb`, `.ods`, templates: a spreadsheet, but not one we read (owner,
    # 2026-11-16). The user is asked to save it as .xlsx or CSV.
    "unsupported_spreadsheet",
    # A password-protected workbook (an encrypted OOXML package).
    "encrypted_spreadsheet",
    "parse_timeout",  # the parsing child process ran out of time (isolate.py)
    "parse_failed",  # the parsing child process died (native crash, memory limit)
]

InputWarningCode = Literal[
    "attachments_ignored",  # EML attachments / forwarded messages were not read
    # EML: the plain-text and HTML versions carry different numbers. The model read the
    # plain text; a mail client shows the HTML (`sec` M3).
    "alternative_parts_differ",
    # EML HTML: elements hidden by the `hidden` attribute, an inline style or a `<style>`
    # rule (display:none, visibility, opacity, tiny fonts, ...): a mail client shows less
    # than the text holds (`sec` M3, delta L-7).
    "hidden_html_text",
    # XLSX: more than one visible sheet holds data; only the first was read. The table
    # carries the other sheets' names (document content: never logged).
    "other_sheets_ignored",
    "hidden_sheets_ignored",  # XLSX: a hidden sheet holds data; it was not read
    # XLSX: hidden rows or columns hold data; they were dropped before anything read
    # them (the table carries the counts).
    "hidden_cells_ignored",
    # XLSX: formulas without a cached value; those cells are empty (never evaluated).
    "formula_without_value",
    "macros_ignored",  # XLSM / a VBA project: never read, never run
    # CSV: not UTF-8 and no UTF-16 BOM; read as Windows-1252.
    "encoding_guessed",
]


class InputError(Exception):
    """A file we can't turn into a document. `str(error)` is the code, nothing else."""

    def __init__(self, code: InputErrorCode) -> None:
        super().__init__(code)
        self.code: InputErrorCode = code

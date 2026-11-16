"""Caps for untrusted files (SPEC section 10, Q12, ADR 0012), all in one place."""

from typing import Final

MAX_FILE_BYTES: Final = 20 * 1024 * 1024  # SPEC section 10
MAX_PDF_PAGES: Final = 20  # Q12

# Images sent to the model: long side at most 1568 px (the provider's recommended size;
# larger is downscaled on its side anyway, at our token cost).
MAX_IMAGE_SIDE: Final = 1568
PDF_RENDER_DPI: Final = 150
# An uploaded image may have at most this many pixels before it is decoded (a 48 MP phone
# photo passes; a decompression bomb doesn't).
MAX_SOURCE_PIXELS: Final = 60_000_000
# A page image is sent as PNG (sharp text) unless that is larger than this; then JPEG.
MAX_PNG_BYTES: Final = 1_000_000
JPEG_QUALITY: Final = 85
# All page images of one document together. The adapter sends them base64-encoded (x 4/3),
# so 20 MiB is ~26.7 MiB on the wire plus at most ~0.8 MB of text, under the provider's
# 32 MB request limit (`sec` L2).
MAX_TOTAL_IMAGE_BYTES: Final = 20 * 1024 * 1024

# A PDF with fewer non-space characters than this in its text layer is treated as a scan.
MIN_TEXT_CHARS: Final = 20
# Text of one document (about 50k tokens): more is not a purchase order we can send.
MAX_TEXT_CHARS: Final = 200_000
# An HTML email body larger than this is refused before conversion (`sec` L3).
MAX_HTML_CHARS: Final = 5_000_000

# EML: the claimed `Date` must fall between this year and one day from now.
MIN_CLAIMED_YEAR: Final = 1990

# Spreadsheets (XLSX/CSV, M1 PR 5). The table handed to the extraction:
MAX_TABLE_ROWS: Final = 2_000  # non-empty rows (Q12); more is `too_many_rows`
MAX_TABLE_COLUMNS: Final = 64  # columns that hold data; more is `document_too_large`
# Rows read, empty ones included: a cell far down the sheet can't make us walk a
# million empty rows. Data below this row is `too_many_rows`.
MAX_ROWS_READ: Final = 20_000
MAX_CELL_CHARS: Final = 32_767  # Excel's own limit per cell
MAX_TABLE_CHARS: Final = 1_000_000
# Excel's grid: column XFD, row 1,048,576. A reference beyond it is `invalid_file`.
MAX_SHEET_COLUMN: Final = 16_384
MAX_SHEET_ROW: Final = 1_048_576
# Names of the other sheets, shown to the reviewer in a warning (owner, 2026-11-16).
MAX_SHEET_NAME_CHARS: Final = 31  # Excel's limit
MAX_SHEET_NAMES: Final = 255

# XLSX is a ZIP: its central directory is checked before anything is decompressed.
MAX_ZIP_ENTRIES: Final = 500
# Central directory bytes per entry (46 + name + extra + comment); Excel's are ~100.
MAX_ZIP_DIRECTORY_BYTES: Final = MAX_ZIP_ENTRIES * 1024
MAX_ZIP_ENTRY_BYTES: Final = 64 * 1024 * 1024  # uncompressed, per entry
MAX_ZIP_TOTAL_BYTES: Final = 256 * 1024 * 1024  # uncompressed, all entries
MAX_SHARED_STRINGS_BYTES: Final = 32 * 1024 * 1024
# `[Content_Types].xml`, read during detection (also in the API process, PR 13).
MAX_CONTENT_TYPES_BYTES: Final = 1024 * 1024
# A legacy Office file (CFB: `.xls`, encrypted OOXML): directory sectors read at most.
MAX_CFB_DIRECTORY_SECTORS: Final = 64
# CSV detection reads this much of the file.
CSV_SNIFF_BYTES: Final = 64 * 1024

# The parsing child process (isolate.py).
PARSE_TIMEOUT_SECONDS: Final = 60.0
CHILD_MEMORY_BYTES: Final = 2 * 1024 * 1024 * 1024  # address-space limit, Linux only

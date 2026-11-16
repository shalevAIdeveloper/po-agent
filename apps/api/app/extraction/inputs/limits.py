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

# The parsing child process (isolate.py).
PARSE_TIMEOUT_SECONDS: Final = 60.0
CHILD_MEMORY_BYTES: Final = 2 * 1024 * 1024 * 1024  # address-space limit, Linux only

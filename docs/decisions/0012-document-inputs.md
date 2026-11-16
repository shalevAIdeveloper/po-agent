# 0012 — Document inputs: detection, parsers, caps and isolation

## Context
M1 turns an uploaded file into the input of the extraction core (`DocumentInput`: a text layer and/or page images, plus untrusted metadata; ADR 0011). Every file is untrusted input (principle 3) and goes through native parsers. The owner decided (Q2, Q12, 2026-11-14): PDF (text and scanned), PNG/JPEG and EML body in M1; XLSX/CSV in PR 5; HEIC and EML attachments in M6; at most 20 PDF pages; 20 MB per file (SPEC §10). The owner's answers on the PR 4 plan (2026-11-16) are recorded below. This ADR covers M1 PR 4. PR 5 adds the spreadsheet path to it.

## Decision
- **Type from the bytes only** (`app/extraction/inputs/detect.py`). The filename and the declared Content-Type are never read.
  - PNG and JPEG by their signatures.
  - PDF by `%PDF-` at the start, or within the first 1 KB.
  - EML by its header block: RFC 5322 field lines up to a blank line, with `From` and at least two known email fields.
  - Everything else is `unsupported_type`: HEIC, GIF, WebP, and for now ZIP/XLSX and CSV.
- **Caps** (`limits.py`, one place):
  - 20 MB per file (`file_too_large`), checked before detection.
  - 20 PDF pages (`too_many_pages`), checked before any page is loaded.
  - 60 MP per uploaded image, read from the header before decoding (`image_too_large`). Pillow's `MAX_IMAGE_PIXELS` is set to the same value, and its bomb warning is turned into an error, as a backstop.
  - 200,000 characters of text, and 24 MB of page images per document (`document_too_large`), so one request stays well under the provider's 32 MB.
- **Images sent to the model** (`image.py`):
  - Only Pillow's PNG/JPEG/MPO decoders are used. MPO is the multi-picture JPEG phones write; only its first frame is used.
  - EXIF orientation is applied, transparency is flattened onto white, and the long side is cut to 1568 px.
  - Every image is **re-encoded** from a fresh RGB image: no EXIF (GPS), ICC profile or text chunk leaves the parser.
  - Pages are encoded as PNG for sharp text, or as JPEG at quality 85 when the PNG would be over 1 MB.
- **PDF** (`pdf.py`, pypdfium2 `~=5.14.0`, which bundles PDFium without JavaScript or XFA):
  - Text layer page by page, each page under a `[page N]` line, so the model can fill a line's `page`.
  - Every page is rendered with its forms at 150 dpi, but never more than 1568 px on the long side. The scale is computed from the page box before rendering, so a 200-inch page can't allocate a 900 MP bitmap.
  - Fewer than 20 non-space characters of text in the whole file means a scan: `text=None`, images only, and grounding is skipped.
  - **A PDF that needs a password to open** is `unsupported_pdf`. **A PDF with only an owner password** (no printing or copying, but opens without a password) **is read normally** (owner, 2026-11-16).
  - Known gap: values typed into form fields are drawn on the page image but are not in the text layer, so grounding may flag them. That is a false alarm the reviewer can see, never a changed value.
- **EML** (`email.py`, stdlib, `policy.default`):
  - The body is the first `text/plain` part, else `text/html` converted to text: `script`/`style`/`head` dropped, block tags become line breaks, table cells become tabs, whitespace collapsed as a browser would show it. Nothing referenced by the HTML is ever fetched.
  - An unknown charset or undecodable bytes are replaced, not fatal.
  - **Attachments are not read**, and that includes a forwarded message (`message/rfc822`) (owner, 2026-11-16). Their presence adds the warning `attachments_ignored`, never their names.
  - **`From` and `Date` are forgeable**: anyone can write any value into the file. They are kept **for display only** (`display_from` = the address part only, ≤ 254 chars; `display_date` = only a date with a time zone). They also go into the prompt as untrusted metadata, inside the document block (ADR 0011).
  - **Nothing may identify the customer, choose a price list or decide anything from them** (owner, 2026-11-16). M6 email intake identifies the customer by the receiving channel and authenticated sender checks, never by the `From` text (Follow-up M6-FROM).
- **Errors are codes** (`errors.py`): `unsupported_type`, `file_too_large`, `too_many_pages`, `unsupported_pdf`, `invalid_file`, `image_too_large`, `empty_document`, `document_too_large`, `parse_timeout`, `parse_failed`. Any other exception from a parser becomes `invalid_file`; its class name goes to the log, its message never does (it can quote the file). `MemoryError` is `parse_failed`.
- **Isolation** (`isolate.py`, owner, 2026-11-16): the worker parses in a **child process** started with `spawn`.
  - The child gets only the bytes, runs only the parsers (no logging, network or DB), and returns the result through a pipe.
  - On Linux the child has a 2 GiB address-space limit.
  - After 60 s the child is killed (`parse_timeout`). A child that dies without an answer, or that raises, is `parse_failed`, with no traceback printed.
  - The parent logs the outcome. PDFium and Pillow are native code: a thread can't stop them, and a crash in them would otherwise take the worker down.
- **Logging**: one line per document, `document.parsed` or `document.parse_rejected`, with kind, size, page and image counts, text length, warning codes and error code. Never a filename, text, header value or parser message. `ParsedDocument` and `ParsedEmail` hide their content from `repr`.
- **Dependencies**:
  - Runtime: `pypdfium2~=5.14.0` (released 2026-10-04) and `pillow~=13.0.0` (2026-10-17). The newer 5.15.x was under two weeks old.
  - Dev only: `reportlab~=5.1.0`, so the tests build every PDF (including encrypted ones) in code. No binary test files are committed.
  - `defusedxml` comes with PR 5 (openpyxl), since nothing here parses XML.

## Consequences
- The worker (PR 14) calls `parse_isolated` through `asyncio.to_thread`. Its job lease must cover the 60 s parse plus the LLM deadline (ADR 0011).
- The API (PR 13) runs detection and the size cap on upload too (415/413) but never the parsers. Parsing happens only in the worker's child process.
- Up to 20 page images per request is real input-token cost. It is measured in PR 9's live eval, and prompt caching is M1-CACHE.
- Parser CVEs: versions are pinned and upgraded deliberately. Dependabot comes with G1.
- Spawning a process costs about 0.5–1 s per document, which is small next to the LLM call.

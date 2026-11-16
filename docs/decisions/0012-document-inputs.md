# 0012 — Document inputs: detection, parsers, caps and isolation

## Context
M1 turns an uploaded file into the input of the extraction core (`DocumentInput`: a text layer and/or page images, plus untrusted metadata; ADR 0011). Every file is untrusted input (principle 3) and goes through native parsers. The owner decided (Q2, Q12, 2026-11-14): PDF (text and scanned), PNG/JPEG and EML body in M1; XLSX/CSV in PR 5; HEIC and EML attachments in M6; at most 20 PDF pages; 20 MB per file (SPEC §10). The owner's answers on the PR 4 plan (2026-11-16) are recorded below. This ADR covers M1 PR 4, including its `sec` and `qa` review fixes. PR 5 adds the spreadsheet path to it.

## Decision
- **Type from the bytes only** (`app/extraction/inputs/detect.py`). The filename and the declared Content-Type are never read.
  - PNG and JPEG by their signatures.
  - PDF by `%PDF-` at the start, or within the first 1 KB (checked after the EML test).
  - EML by its header block, read within the first 64 KB: RFC 5322 field lines up to a blank line, with `From` and at least two known email fields. An mbox `From ` separator as the first line is allowed.
  - Everything else is `unsupported_type`: HEIC, GIF, WebP, and for now ZIP/XLSX and CSV.
- **Caps** (`limits.py`, one place):
  - 20 MB per file (`file_too_large`), checked before detection and before any child process starts.
  - 20 PDF pages (`too_many_pages`), checked before any page is loaded.
  - 60 MP per uploaded image, read from the header before decoding (`image_too_large`). Pillow's `MAX_IMAGE_PIXELS` is set to the same value, and its bomb warning is turned into an error, as a backstop. Each guard is tested without the other.
  - 200,000 characters of text (`document_too_large`). A PDF's text is checked page by page, before each page is rendered. HTML over 5 M characters is refused before conversion.
  - 20 MiB of page images per document (`document_too_large`). The adapter base64-encodes images, so that is about 26.7 MiB on the wire, plus at most about 0.8 MB of text, which keeps one request under the provider's 32 MB limit. A test pins this arithmetic.
- **Images sent to the model** (`image.py`):
  - Only Pillow's PNG and JPEG decoders are used. A phone's multi-picture JPEG (MPO) opens through the JPEG decoder, and only its first picture is used.
  - EXIF orientation is applied, transparency is flattened onto white, and the long side is cut to 1568 px.
  - Every image is **re-encoded** from a fresh RGB image: no EXIF (GPS), ICC, XMP or text chunk leaves the parser.
  - Pages are encoded as PNG for sharp text, or as JPEG at quality 85 when the PNG would be over 1 MB.
- **PDF** (`pdf.py`, pypdfium2 `~=5.14.0`, which bundles PDFium without JavaScript or XFA):
  - Text layer page by page, each page under a `[page N]` line, so the model can fill a line's `page`.
  - Every page is rendered with its forms at 150 dpi, but never more than 1568 px on the long side. The scale is computed from the page box before rendering, so a 200-inch page can't allocate a 900 MP bitmap.
  - Fewer than 20 non-space characters of text in the whole file means a scan: `text=None`, images only, and grounding is skipped.
  - **A PDF that needs a password to open** is `unsupported_pdf`. **A PDF with only an owner password** (no printing or copying, but opens without a password) **is read normally** (owner, 2026-11-16).
  - Known gaps:
    - Values typed into form fields are drawn on the page image but are not in the text layer, so grounding may flag them. That is a false alarm the reviewer can see, never a changed value.
    - Text can be in the text layer without being visible (render mode 3, white text). OCR'd scans rely on that, so it is kept. See "What the reviewer sees" below.
- **EML** (`email.py`, stdlib, `policy.default`):
  - **Body:** the `text/plain` part. If there is no plain part, or it is blank, the `text/html` part is converted to text instead (`qa`): `script`/`style`/`head` dropped (and a `<body>` tag always ends the head, since HTML5 lets `</head>` be left out), block tags become line breaks, table cells become tabs, whitespace collapsed as a browser would show it. Nothing referenced by the HTML is ever fetched.
  - An unknown charset or undecodable bytes are replaced, not fatal.
  - **Attachments are not read**, and that includes a forwarded message (`message/rfc822`) (owner, 2026-11-16). Their presence adds the warning `attachments_ignored`, never their names.
  - **Warnings for text the reader can't see** (`sec` M3):
    - `alternative_parts_differ` when the plain and HTML versions carry different numbers. The model reads the plain text, while a mail client shows the HTML.
    - `hidden_html_text` when the HTML that was read hides elements with the `hidden` attribute or inline `display:none`, `visibility:hidden` or `font-size:0`.
    - Hidden text is kept, not removed. The reviewer sees the warning.
  - **`From` and `Date` are claims anyone can forge.** They are kept as `claimed_from` (the address part only) and `claimed_date` (only a date with a time zone), **for display only** (owner, 2026-11-16).
    - They also go into the prompt as untrusted metadata, inside the document block (ADR 0011).
    - A `From` with control or format characters, or that isn't valid UTF-8, becomes `None`. So does a `Date` outside 1990 to tomorrow, or one that can't be converted to UTC (`sec` L1).
    - **Nothing may store them as the received time, identify the customer, choose a price list or decide anything from them.** M6 email intake identifies the customer by the receiving channel and by sender authentication checked by our own receiving server, never by the `From` text (Follow-up M6-FROM).
- **What the reviewer sees** (`sec` M3): the review screen (M3) shows the text the model actually read, plus the page images. It never shows a rendered HTML email. So text the model used is never hidden from the reviewer (Follow-up M3-SOURCE).
- **Errors are codes** (`errors.py`): `unsupported_type`, `file_too_large`, `too_many_pages`, `unsupported_pdf`, `invalid_file`, `image_too_large`, `empty_document`, `document_too_large`, `parse_timeout`, `parse_failed`. Any other exception from a parser becomes `invalid_file`; its class name goes to the log, its message never does (it can quote the file). `MemoryError` is `parse_failed`.
- **Isolation** (`isolate.py` + `child.py`, owner, 2026-11-16; hardened after `sec` M1, M2, L4): the worker parses in a **child process started with `subprocess`**.
  - The child is a fresh `python -I -B` interpreter with an **empty environment**: only `PATH`, plus `SYSTEMROOT` on Windows. So the child has no `DATABASE_URL`, `LLM_API_KEY` or any other secret; a test checks this. (`multiprocessing`'s `spawn` copied the whole environment.)
  - The file goes in on stdin. No other descriptors are inherited, and stderr goes to devnull.
  - On Linux the child sets a 2 GiB address-space limit before reading the file.
  - The child answers with a **JSON header plus raw image bytes** (`wire.py`), never a pickle. The parent checks the reply like untrusted input: allowed literals, types, lengths that add up, and the same caps as the parsers. The parent reads at most the largest valid reply size.
  - After 60 s the child is killed (`parse_timeout`). A non-zero exit, no answer, too much output or a malformed reply is `parse_failed`. Tests cover a hang, a crash, a raised exception, garbage output, a flood, and a child that closes stdout and keeps running.
- **Logging**: one line per document, `document.parsed` or `document.parse_rejected`, with kind, size, page and image counts, text length, warning codes and error code. Never a filename, text, header value or parser message. The parent logs; the child doesn't. `ParsedDocument` and `ParsedEmail` hide their content from `repr`.
- **Dependencies**:
  - Runtime: `pypdfium2~=5.14.0` (released 2026-10-04) and `pillow~=13.0.0` (2026-10-17). The newer 5.15.x was under two weeks old.
  - Dev only: `reportlab~=5.1.0`, so the tests build every PDF (including encrypted ones and forms) in code. No binary test files are committed. The image smoke check (`smoke-images.sh`) fails if `reportlab` is installed in the API image.
  - `defusedxml` comes with PR 5 (openpyxl), since nothing here parses XML.

## Consequences
- The worker (PR 14) calls `parse_isolated` through `asyncio.to_thread`. Its job lease must cover the 60 s parse plus the LLM deadline (ADR 0011).
- Small hostile files can still use the full 60 s, for example dense text operators or 200,000 MIME parts. That costs worker time, not safety. PR 13/14 limit parses per org and run at most (container memory ÷ 2 GiB) children at once (Follow-up M1-PARSE-LOAD).
- The API (PR 13) runs detection and the size cap on upload too (415/413) but never the parsers. Parsing happens only in the worker's child process.
- Up to 20 page images per request is real input-token cost. It is measured in PR 9's live eval, and prompt caching is M1-CACHE.
- Parser CVEs: versions are pinned and upgraded deliberately. Dependabot comes with G1.
- Starting a child costs about 0.5–1 s per document, which is small next to the LLM call.

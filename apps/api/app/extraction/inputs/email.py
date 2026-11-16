"""EML with the stdlib parser (`policy.default`): the body only (Q2).

- Body: the first text/plain part, else text/html turned into text (scripts, styles and
  `<head>` dropped, block tags as line breaks, table cells as tabs). Nothing referenced by
  the HTML is ever fetched.
- Attachments (including a forwarded message, `message/rfc822`) are not read; their
  presence adds an `attachments_ignored` warning, never their names (owner, 2026-11-16).
- `From` and `Date` are forgeable by whoever sent the file. They are kept **for display
  only** (`display_from`, `display_date`) and as untrusted metadata in the prompt; nothing
  may identify the customer or decide anything from them (owner, 2026-11-16; ADR 0012).
- A charset Python doesn't know, or bytes that don't decode, are replaced, not fatal.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from email import policy
from email.headerregistry import Address
from email.message import EmailMessage, MIMEPart
from email.parser import BytesParser
from html.parser import HTMLParser
from typing import Final

from app.extraction.inputs.errors import InputError, InputWarningCode

MAX_ADDRESS_CHARS: Final = 254
_SKIPPED_TAGS: Final = frozenset({"script", "style", "head", "template", "noscript"})
_BREAK_TAGS: Final = frozenset(
    {
        "br",
        "p",
        "div",
        "tr",
        "li",
        "ul",
        "ol",
        "table",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "pre",
        "blockquote",
        "hr",
        "section",
        "article",
    }
)
_CELL_TAGS: Final = frozenset({"td", "th"})
_HTML_SPACE_RE: Final = re.compile(r"[ \t\n\r\f]+")


@dataclass(frozen=True, slots=True)
class ParsedEmail:
    text: str = field(repr=False)
    display_from: str | None = field(repr=False)
    display_date: datetime | None
    warnings: tuple[InputWarningCode, ...]


class _TextFromHtml(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED_TAGS:
            self._skipping += 1
        elif tag in _BREAK_TAGS:
            self._parts.append("\n")
        elif tag in _CELL_TAGS:
            self._parts.append("\t")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BREAK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_TAGS:
            self._skipping = max(0, self._skipping - 1)
        elif tag in _BREAK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            # Whitespace in HTML source is layout, not content (as a browser shows it).
            self._parts.append(_HTML_SPACE_RE.sub(" ", data))

    def text(self) -> str:
        # Cells are tab-separated within a line; empty lines (between rows, paragraphs)
        # carry no data and are dropped.
        lines = (line.strip(" \t") for line in "".join(self._parts).split("\n"))
        return "\n".join(line for line in lines if line)


def html_to_text(html: str) -> str:
    parser = _TextFromHtml()
    parser.feed(html)
    parser.close()
    return parser.text()


def _content(part: MIMEPart) -> str:
    try:
        content = part.get_content()
    except (LookupError, UnicodeError, ValueError):
        payload = part.get_payload(decode=True)
        content = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else ""
    return content if isinstance(content, str) else ""


def _display_from(message: EmailMessage) -> str | None:
    try:
        header = message["From"]
        addresses: tuple[Address, ...] = getattr(header, "addresses", ())
    except (IndexError, ValueError, TypeError, AttributeError):
        return None
    for address in addresses:
        spec = address.addr_spec
        if address.username and address.domain and len(spec) <= MAX_ADDRESS_CHARS:
            return spec
    return None


def _display_date(message: EmailMessage) -> datetime | None:
    try:
        value = getattr(message["Date"], "datetime", None)
    except (IndexError, ValueError, TypeError, AttributeError, OverflowError):
        return None
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value
    return None  # a date without a time zone can't be placed in time


def parse_email(data: bytes) -> ParsedEmail:
    message = BytesParser(policy=policy.default).parsebytes(data)
    if not isinstance(message, EmailMessage):
        raise InputError("invalid_file")
    body = message.get_body(preferencelist=("plain", "html"))
    text = ""
    if body is not None:
        content = _content(body)
        text = html_to_text(content) if body.get_content_subtype() == "html" else content
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise InputError("empty_document")
    has_attachments = message.is_multipart() and any(True for _ in message.iter_attachments())
    return ParsedEmail(
        text=text,
        display_from=_display_from(message),
        display_date=_display_date(message),
        warnings=("attachments_ignored",) if has_attachments else (),
    )

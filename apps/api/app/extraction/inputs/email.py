"""EML with the stdlib parser (`policy.default`): the body only (Q2).

- Body: the `text/plain` part, or `text/html` turned into text when there is no plain part
  or it is blank (`qa`, 2026-11-16). HTML conversion drops scripts, styles and `<head>`,
  turns block tags into line breaks and table cells into tabs, and never fetches anything.
  HTML over MAX_HTML_CHARS is refused before conversion (`sec` L3).
- What the model reads can differ from what a mail client shows (`sec` M3):
  `alternative_parts_differ` when the plain and HTML versions carry different numbers,
  `hidden_html_text` when the HTML that was read hides elements with inline styles. The
  review screen shows the text the model read, never a rendered HTML email (ADR 0012).
- Attachments (including a forwarded message, `message/rfc822`) are not read; their
  presence adds an `attachments_ignored` warning, never their names (owner, 2026-11-16).
- `From` and `Date` are claims anyone can forge: kept as `claimed_from` / `claimed_date`
  for display only (owner, 2026-11-16; ADR 0012). A value with control/format characters,
  that isn't valid UTF-8, or a date outside 1990..tomorrow is dropped (`sec` L1).
- A charset Python doesn't know, or bytes that don't decode, are replaced, not fatal.
"""

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email import policy
from email.headerregistry import Address
from email.message import EmailMessage, MIMEPart
from email.parser import BytesParser
from html.parser import HTMLParser
from typing import Final

from app.extraction.inputs.errors import InputError, InputWarningCode
from app.extraction.inputs.limits import MAX_HTML_CHARS, MIN_CLAIMED_YEAR

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
_HIDING_STYLE_RE: Final = re.compile(
    r"display:none|visibility:hidden|font-size:0(?:\.0*)?(?:px|pt|em|rem|%)?(?:;|!|$)"
)
_NUMBER_RE: Final = re.compile(r"[0-9]+(?:[.,][0-9]+)*")


@dataclass(frozen=True, slots=True)
class ParsedEmail:
    text: str = field(repr=False)
    claimed_from: str | None = field(repr=False)
    claimed_date: datetime | None
    warnings: tuple[InputWarningCode, ...]


class _TextFromHtml(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skipping = 0
        self.hides_text = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "body":
            # HTML5 lets `</head>` be left out: the body starts here whatever came before.
            self._skipping = 0
        if tag in _SKIPPED_TAGS:
            self._skipping += 1
        elif tag in _BREAK_TAGS:
            self._parts.append("\n")
        elif tag in _CELL_TAGS:
            self._parts.append("\t")
        if not self._skipping and _hides(attrs):
            self.hides_text = True

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


def _hides(attrs: list[tuple[str, str | None]]) -> bool:
    for name, value in attrs:
        if name == "hidden":
            return True
        if name == "style" and value:
            compact = "".join(value.split()).lower()
            if _HIDING_STYLE_RE.search(compact):
                return True
    return False


def _convert(html: str) -> _TextFromHtml:
    if len(html) > MAX_HTML_CHARS:
        raise InputError("document_too_large")
    parser = _TextFromHtml()
    parser.feed(html)
    parser.close()
    return parser


def html_to_text(html: str) -> str:
    return _convert(html).text()


def _content(part: MIMEPart) -> str:
    try:
        content = part.get_content()
    except (LookupError, UnicodeError, ValueError):
        payload = part.get_payload(decode=True)
        content = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else ""
    text = content if isinstance(content, str) else ""
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _numbers(text: str) -> Counter[str]:
    return Counter(number.replace(",", "") for number in _NUMBER_RE.findall(text))


def _printable(value: str) -> bool:
    if any(unicodedata.category(char).startswith("C") for char in value):
        return False  # controls, format characters, lone surrogates from raw 8-bit headers
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _claimed_from(message: EmailMessage) -> str | None:
    try:
        header = message["From"]
        addresses: tuple[Address, ...] = getattr(header, "addresses", ())
    except (IndexError, ValueError, TypeError, AttributeError):
        return None
    for address in addresses:
        spec = address.addr_spec
        if (
            address.username
            and address.domain
            and len(spec) <= MAX_ADDRESS_CHARS
            and _printable(spec)
        ):
            return spec
    return None


def _claimed_date(message: EmailMessage) -> datetime | None:
    try:
        value = getattr(message["Date"], "datetime", None)
        if not isinstance(value, datetime) or value.tzinfo is None:
            return None  # a date without a time zone can't be placed in time
        in_utc = value.astimezone(UTC)
    except (IndexError, ValueError, TypeError, AttributeError, OverflowError):
        return None
    if in_utc.year < MIN_CLAIMED_YEAR or in_utc > datetime.now(UTC) + timedelta(days=1):
        return None
    return value


def _body(message: EmailMessage) -> tuple[str, tuple[InputWarningCode, ...]]:
    plain_part = message.get_body(preferencelist=("plain",))
    html_part = message.get_body(preferencelist=("html",))
    plain = _content(plain_part) if plain_part is not None else ""
    html = _convert(_content(html_part)) if html_part is not None else None
    warnings: list[InputWarningCode] = []
    if plain:
        text = plain
        if html is not None and _numbers(html.text()) != _numbers(plain):
            warnings.append("alternative_parts_differ")
    elif html is not None:
        text = html.text()
        if html.hides_text:
            warnings.append("hidden_html_text")
    else:
        text = ""
    return text, tuple(warnings)


def parse_email(data: bytes) -> ParsedEmail:
    message = BytesParser(policy=policy.default).parsebytes(data)
    if not isinstance(message, EmailMessage):
        raise InputError("invalid_file")
    text, warnings = _body(message)
    if not text:
        raise InputError("empty_document")
    if message.is_multipart() and any(True for _ in message.iter_attachments()):
        warnings = ("attachments_ignored", *warnings)
    return ParsedEmail(
        text=text,
        claimed_from=_claimed_from(message),
        claimed_date=_claimed_date(message),
        warnings=warnings,
    )

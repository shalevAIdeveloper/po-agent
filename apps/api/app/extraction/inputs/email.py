"""EML with the stdlib parser (`policy.default`): the body only (Q2).

- Body: the `text/plain` part, or `text/html` turned into text when there is no plain part
  or it is blank (`qa`, 2026-11-16). HTML conversion drops scripts, styles and `<head>`,
  turns block tags into line breaks and table cells into tabs, and never fetches anything.
  HTML over MAX_HTML_CHARS is refused before conversion (`sec` L3).
- What the model reads can differ from what a mail client shows (`sec` M3):
  `alternative_parts_differ` when the plain and HTML versions carry different numbers (or
  the same numbers in a different order), `hidden_html_text` when the HTML hides elements
  (the `hidden` attribute, an inline style, or a `<style>` rule whose selector the markup
  uses), whichever part was read. These are heuristics, not a renderer; the review screen
  shows the text the model read, never a rendered HTML email (ADR 0012).
- Attachments (including a forwarded message, `message/rfc822`) are not read; their
  presence adds an `attachments_ignored` warning, never their names (owner, 2026-11-16).
- `From` and `Date` are claims anyone can forge: kept as `claimed_from` / `claimed_date`
  for display only (owner, 2026-11-16; ADR 0012). A value with control/format characters,
  that isn't valid UTF-8, or a date outside 1990..tomorrow is dropped (`sec` L1).
- A charset Python doesn't know, or bytes that don't decode, are replaced, not fatal.
"""

import re
import unicodedata
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
_NUMBER_RE: Final = re.compile(r"[0-9]+(?:[.,][0-9]+)*")
# Hiding CSS (`sec` M3, delta L-7). A warning, not a renderer: it errs towards flagging.
_HIDING_DECLARATIONS: Final = frozenset(
    {
        ("display", "none"),
        ("visibility", "hidden"),
        ("visibility", "collapse"),
        ("mso-hide", "all"),
    }
)
# Below these a font size is unreadable: px/unitless, pt, em/rem, %.
_SMALLEST_FONT: Final = {"": 2.0, "px": 2.0, "pt": 1.5, "em": 0.15, "rem": 0.15, "%": 15.0}
_CSS_NUMBER_RE: Final = re.compile(r"([0-9]+(?:\.[0-9]*)?|\.[0-9]+)([a-z%]*)")
_MAX_CSS_VALUE_CHARS: Final = 32
_CLASS_RE: Final = re.compile(r"\.([\w-]+)")
_ID_RE: Final = re.compile(r"#([\w-]+)")
_TAG_RE: Final = re.compile(r"[a-z][a-z0-9-]*")


def _declaration_hides(prop: str, value: str) -> bool:
    if (prop, value) in _HIDING_DECLARATIONS:
        return True
    if prop not in ("font-size", "max-height", "opacity") or len(value) > _MAX_CSS_VALUE_CHARS:
        return False
    match = _CSS_NUMBER_RE.fullmatch(value)
    if match is None:
        return False
    number, unit = float(match[1]), match[2]
    if prop == "font-size":
        return unit in _SMALLEST_FONT and number < _SMALLEST_FONT[unit]
    if prop == "max-height":
        return number == 0
    return number < (10 if unit == "%" else 0.1)  # opacity


def _declarations_hide(declarations: str) -> bool:
    """`prop: value; ...` (an inline style or a rule's body). Split, never a regex scan."""
    for declaration in declarations.split(";"):
        prop, _, value = declaration.partition(":")
        value = value.partition("!")[0]  # drop `!important`
        if _declaration_hides("".join(prop.split()).lower(), "".join(value.split()).lower()):
            return True
    return False


def _hiding_selectors(css: str) -> list[str]:
    """The last compound selector of every rule in a `<style>` block that hides."""
    compounds: list[str] = []
    for chunk in css.lower().split("}"):
        head, brace, body = chunk.rpartition("{")
        if not brace or not _declarations_hide(body):
            continue
        selectors = head.rpartition("{")[2]  # inside `@media ... {`: the inner rule's
        for selector in selectors.split(","):
            parts = selector.replace(">", " ").replace("+", " ").replace("~", " ").split()
            if parts:
                compounds.append(parts[-1])
    return compounds


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
        self._in_style = 0
        self._css: list[str] = []
        self._inline_hiding = False
        # What the visible markup uses, to tell whether a hiding `<style>` rule applies.
        self._tags: set[str] = set()
        self._classes: set[str] = set()
        self._ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "body":
            # HTML5 lets `</head>` be left out: the body starts here whatever came before.
            self._skipping = 0
        if tag == "style":
            self._in_style += 1
        if tag in _SKIPPED_TAGS:
            self._skipping += 1
        elif tag in _BREAK_TAGS:
            self._parts.append("\n")
        elif tag in _CELL_TAGS:
            self._parts.append("\t")
        if not self._skipping:
            self._record(tag, attrs)

    def _record(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._tags.add(tag)
        for name, value in attrs:
            if name == "hidden" or (value and name == "style" and _declarations_hide(value)):
                self._inline_hiding = True
            elif value and name == "class":
                self._classes.update(value.lower().split())
            elif value and name == "id":
                self._ids.add(value.strip().lower())

    def _applies(self, compound: str) -> bool:
        if compound == "*":
            return True
        if classes := _CLASS_RE.findall(compound):
            return any(name in self._classes for name in classes)
        if ids := _ID_RE.findall(compound):
            return any(name in self._ids for name in ids)
        tag = _TAG_RE.match(compound)
        return tag is not None and tag[0] in self._tags

    def hides_text(self) -> bool:
        """Inline hiding, or a `<style>` rule that hides something the markup uses."""
        if self._inline_hiding:
            return True
        return any(self._applies(c) for c in _hiding_selectors("".join(self._css)))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BREAK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "style":
            self._in_style = max(0, self._in_style - 1)
        if tag in _SKIPPED_TAGS:
            self._skipping = max(0, self._skipping - 1)
        elif tag in _BREAK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self._css.append(data)
        elif not self._skipping:
            # Whitespace in HTML source is layout, not content (as a browser shows it).
            self._parts.append(_HTML_SPACE_RE.sub(" ", data))

    def text(self) -> str:
        # Cells are tab-separated within a line; empty lines (between rows, paragraphs)
        # carry no data and are dropped.
        lines = (line.strip(" \t") for line in "".join(self._parts).split("\n"))
        return "\n".join(line for line in lines if line)


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


def _numbers(text: str) -> list[str]:
    """The numbers in order: the same numbers on different lines also differ (L-7)."""
    return [number.replace(",", "") for number in _NUMBER_RE.findall(text)]


def _printable(value: str) -> bool:
    if any(unicodedata.category(char).startswith("C") for char in value):
        return False  # controls, format characters, lone surrogates from raw 8-bit headers
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def acceptable_address(spec: str) -> bool:
    """`local@domain`, printable, at most MAX_ADDRESS_CHARS. Also checked by `wire.decode`."""
    local, at, domain = spec.rpartition("@")
    return bool(local and at and domain) and len(spec) <= MAX_ADDRESS_CHARS and _printable(spec)


def acceptable_date(value: datetime) -> bool:
    """Has a time zone and falls in MIN_CLAIMED_YEAR..tomorrow. Also checked by `wire`."""
    if value.tzinfo is None or value.utcoffset() is None:
        return False  # a date without a time zone can't be placed in time
    try:
        in_utc = value.astimezone(UTC)
    except (ValueError, OverflowError):
        return False
    return in_utc.year >= MIN_CLAIMED_YEAR and in_utc <= datetime.now(UTC) + timedelta(days=1)


def _claimed_from(message: EmailMessage) -> str | None:
    try:
        header = message["From"]
        addresses: tuple[Address, ...] = getattr(header, "addresses", ())
    except (IndexError, ValueError, TypeError, AttributeError):
        return None
    for address in addresses:
        spec = address.addr_spec
        if address.username and address.domain and acceptable_address(spec):
            return spec
    return None


def _claimed_date(message: EmailMessage) -> datetime | None:
    try:
        value = getattr(message["Date"], "datetime", None)
    except (IndexError, ValueError, TypeError, AttributeError, OverflowError):
        return None
    if not isinstance(value, datetime) or not acceptable_date(value):
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
    else:
        text = html.text() if html is not None else ""
    # Also when the plain part was read: hidden HTML marks a crafted email (L-7).
    if html is not None and html.hides_text():
        warnings.append("hidden_html_text")
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

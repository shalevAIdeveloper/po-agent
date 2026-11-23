"""Versioned prompts: `app/prompts/<name>/v<N>.md` (CLAUDE.md, ADR 0011).

A prompt file has `## <Section>` headings (letters only); `System` and `User` are required.
Text before the first heading is a title and is ignored (but hashed). Any other line that
starts with `#` (`## System Prompt`, `### Retry`, `##Retry`, an indented `## User`) is an
error, so a misspelled heading can't silently become part of the section before it.
Placeholders are `{{lower_snake}}`; a brace touching one (`{{{x}}}`) or any other run of
three braces is an error (M1-BRACES):

- The system section has none: document data never reaches the system prompt.
- Substitution is strict (a missing or unknown value is an error) and single-pass, so a
  value that contains `{{x}}` stays literal text.
- `prompt_version` (`<name>/v<N>`) and the SHA-256 of the file (LF line endings) are
  recorded per call. A version is immutable once a committed eval baseline used it (Q15);
  PR 9 pins the hashes of the baseline's versions in a test.
"""

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Final

PROMPTS_DIR: Final = Path(__file__).resolve().parent.parent / "prompts"

_NAME_RE: Final = re.compile(r"^[a-z][a-z0-9_]*$")
_HEADING_RE: Final = re.compile(r"^## ([A-Za-z]+)[ \t]*$", re.MULTILINE)
# Any line that starts with `#` (after any whitespace, incl. NBSP) is meant as a heading.
_HEADING_LIKE_RE: Final = re.compile(r"^[^\S\n]*#[^\n]*", re.MULTILINE)
# Line breaks other than LF (after CRLF → LF) would hide a heading from `^` (`sec` L4).
_OTHER_LINE_BREAKS: Final = (0x0B, 0x0C, 0x0D, 0x1C, 0x1D, 0x1E, 0x85, 0x2028, 0x2029)
_OTHER_LINE_BREAK_RE: Final = re.compile(
    "[" + "".join(re.escape(chr(code)) for code in _OTHER_LINE_BREAKS) + "]"
)
_TITLE_RE: Final = re.compile(r"# [^\n]*")
_PLACEHOLDER_RE: Final = re.compile(r"\{\{([a-z][a-z0-9_]*)\}\}")
# A brace touching a placeholder (`{{{name}}}`, `{{name}}}`) always forms a run of three;
# the placeholder regex would match inside it and leave a stray `{`/`}` (M1-BRACES).
_TRIPLE_BRACE_RE: Final = re.compile(r"\{\{\{|\}\}\}")
_REQUIRED_SECTIONS: Final = ("System", "User")


class PromptError(Exception):
    """A prompt file or a render call is wrong: a bug in our code, never document data."""


@dataclass(frozen=True, slots=True)
class Prompt:
    name: str
    version: int
    sha256: str
    sections: Mapping[str, str]

    @property
    def prompt_version(self) -> str:
        return f"{self.name}/v{self.version}"

    @property
    def system(self) -> str:
        return self.sections["System"]

    def _section(self, section: str) -> str:
        try:
            return self.sections[section]
        except KeyError:
            raise PromptError(f"{self.prompt_version}: no section {section!r}") from None

    def render(self, section: str, **values: str) -> str:
        text = self._section(section)
        _check_values(self.prompt_version, section, set(_placeholders(text)), values)
        return _substitute(text, values)

    def split(self, section: str, slot: str, **values: str) -> tuple[str, str]:
        """The rendered text before and after `{{slot}}`, which must occur exactly once.

        Lets a caller put non-text content (page images) at the slot's position.
        """
        text = self._section(section)
        names = _placeholders(text)
        if names.count(slot) != 1:
            raise PromptError(f"{self.prompt_version}: {{{{{slot}}}}} must occur exactly once")
        _check_values(self.prompt_version, section, set(names) - {slot}, values)
        before, after = text.split("{{" + slot + "}}")
        return _substitute(before, values), _substitute(after, values)


def _placeholders(text: str) -> list[str]:
    return _PLACEHOLDER_RE.findall(text)


def _check_values(
    prompt_version: str, section: str, expected: set[str], values: Mapping[str, str]
) -> None:
    missing = sorted(expected - set(values))
    unknown = sorted(set(values) - expected)
    if missing or unknown:
        detail = "; ".join(
            part
            for part in (
                f"missing: {', '.join(missing)}" if missing else "",
                f"unknown: {', '.join(unknown)}" if unknown else "",
            )
            if part
        )
        raise PromptError(f"{prompt_version} {section}: {detail}")


def _substitute(text: str, values: Mapping[str, str]) -> str:
    return _PLACEHOLDER_RE.sub(lambda match: values[match.group(1)], text)


def _check_heading_lines(label: str, text: str, headings: list[re.Match[str]]) -> None:
    """Every `#` line is a section heading, or a `# title` line before the first section."""
    first_section = headings[0].start() if headings else len(text)
    section_starts = {heading.start() for heading in headings}
    for line in _HEADING_LIKE_RE.finditer(text):
        if line.start() in section_starts:
            continue
        if line.start() < first_section and _TITLE_RE.fullmatch(line.group()):
            continue
        line_no = text.count("\n", 0, line.start()) + 1
        raise PromptError(f"{label}: unrecognized heading on line {line_no}")


def parse_prompt(name: str, version: int, text: str) -> Prompt:
    label = f"{name}/v{version}"
    text = text.replace("\r\n", "\n")
    if _OTHER_LINE_BREAK_RE.search(text):
        raise PromptError(f"{label}: line break other than LF or CRLF")
    headings = list(_HEADING_RE.finditer(text))
    _check_heading_lines(label, text, headings)
    sections: dict[str, str] = {}
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        title = heading.group(1)
        if title in sections:
            raise PromptError(f"{label}: duplicate section {title!r}")
        body = text[heading.end() : end].strip()
        if not body:
            raise PromptError(f"{label}: empty section {title!r}")
        leftover = _PLACEHOLDER_RE.sub("", body)
        if "{{" in leftover or "}}" in leftover or _TRIPLE_BRACE_RE.search(body):
            raise PromptError(f"{label}: malformed placeholder in section {title!r}")
        sections[title] = body
    for required in _REQUIRED_SECTIONS:
        if required not in sections:
            raise PromptError(f"{label}: missing section {required!r}")
    if _placeholders(sections["System"]):
        raise PromptError(f"{label}: the System section must not have placeholders")
    return Prompt(
        name=name,
        version=version,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        sections=sections,
    )


def load_prompt(name: str, version: int, *, directory: Path = PROMPTS_DIR) -> Prompt:
    if not _NAME_RE.fullmatch(name) or type(version) is not int or version < 1:
        raise PromptError("invalid prompt name or version")
    return _load(name, version, directory)


@cache
def _load(name: str, version: int, directory: Path) -> Prompt:
    path = directory / name / f"v{version}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PromptError(f"{name}/v{version}: prompt file not found") from None
    return parse_prompt(name, version, text)

"""Versioned prompts: `app/prompts/<name>/v<N>.md` (CLAUDE.md, ADR 0011).

A prompt file has `## <Section>` headings; `System` and `User` are required. Text before the
first heading is a title and is ignored (but hashed). Placeholders are `{{lower_snake}}`:

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
_PLACEHOLDER_RE: Final = re.compile(r"\{\{([a-z][a-z0-9_]*)\}\}")
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


def parse_prompt(name: str, version: int, text: str) -> Prompt:
    label = f"{name}/v{version}"
    text = text.replace("\r\n", "\n")
    headings = list(_HEADING_RE.finditer(text))
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
        if "{{" in leftover or "}}" in leftover:
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

import hashlib
from pathlib import Path

import pytest

from app.llm.prompts import PROMPTS_DIR, PromptError, load_prompt, parse_prompt

GOOD = """# demo — v1

## System
You extract things.

## User
Hello {{name}}.
{{document}}
Bye.
"""


def write(tmp_path: Path, text: str, name: str = "demo", version: int = 1) -> Path:
    folder = tmp_path / name
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"v{version}.md"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def test_loads_sections_version_and_hash(tmp_path: Path) -> None:
    write(tmp_path, GOOD)
    prompt = load_prompt("demo", 1, directory=tmp_path)

    assert prompt.prompt_version == "demo/v1"
    assert prompt.system == "You extract things."
    assert prompt.sections["User"] == "Hello {{name}}.\n{{document}}\nBye."
    assert prompt.sha256 == hashlib.sha256(GOOD.encode("utf-8")).hexdigest()


def test_crlf_file_hashes_like_lf(tmp_path: Path) -> None:
    # A Windows checkout must not change the recorded hash (or the replay fingerprint).
    lf = parse_prompt("demo", 1, GOOD)
    crlf = parse_prompt("demo", 1, GOOD.replace("\n", "\r\n"))
    assert crlf.sha256 == lf.sha256
    assert crlf.sections == lf.sections


def test_render_substitutes_exactly_the_declared_placeholders() -> None:
    prompt = parse_prompt("demo", 1, GOOD)
    assert prompt.render("User", name="Ann", document="DOC") == "Hello Ann.\nDOC\nBye."


def test_render_rejects_a_missing_value() -> None:
    prompt = parse_prompt("demo", 1, GOOD)
    with pytest.raises(PromptError, match="missing: document"):
        prompt.render("User", name="Ann")


def test_render_rejects_an_unknown_value() -> None:
    prompt = parse_prompt("demo", 1, GOOD)
    with pytest.raises(PromptError, match="unknown: other"):
        prompt.render("User", name="Ann", document="d", other="x")


def test_values_are_not_expanded_again() -> None:
    # A document containing "{{name}}" stays literal text: one pass, no re-expansion.
    prompt = parse_prompt("demo", 1, GOOD)
    out = prompt.render("User", name="Ann", document="{{name}} {{other}}")
    assert out == "Hello Ann.\n{{name}} {{other}}\nBye."


def test_split_returns_the_text_around_a_slot() -> None:
    prompt = parse_prompt("demo", 1, GOOD)
    before, after = prompt.split("User", "document", name="Ann")
    assert before == "Hello Ann.\n"
    assert after == "\nBye."


def test_split_needs_the_slot_exactly_once() -> None:
    twice = GOOD.replace("Bye.", "{{document}}")
    prompt = parse_prompt("demo", 1, twice)
    with pytest.raises(PromptError, match="exactly once"):
        prompt.split("User", "document", name="Ann")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (GOOD.replace("## System", "## Sys"), "System"),
        (GOOD.replace("## User", "## Usr"), "User"),
        (GOOD + "\n## User\nagain\n", "duplicate section"),
        (GOOD.replace("You extract things.", "You extract {{name}}."), "System"),
        (GOOD.replace("{{name}}", "{{ name }}"), "malformed placeholder"),
        (GOOD.replace("{{name}}", "{name}}"), "malformed placeholder"),
        (GOOD.replace("Bye.", "Bye }}"), "malformed placeholder"),
        (GOOD.replace("You extract things.", ""), "empty section"),
    ],
)
def test_malformed_prompt_files_fail_to_load(text: str, message: str) -> None:
    with pytest.raises(PromptError, match=message):
        parse_prompt("demo", 1, text)


@pytest.mark.parametrize(
    ("name", "version"), [("../x", 1), ("Demo", 1), ("demo", 0), ("", 1), ("demo", True)]
)
def test_bad_name_or_version_is_refused(tmp_path: Path, name: str, version: int) -> None:
    write(tmp_path, GOOD)
    with pytest.raises(PromptError):
        load_prompt(name, version, directory=tmp_path)


def test_split_substitutes_both_sides_and_keeps_values_literal() -> None:
    prompt = parse_prompt("demo", 1, GOOD.replace("Bye.", "Bye {{name}}."))
    assert prompt.split("User", "document", name="N") == ("Hello N.\n", "\nBye N.")
    before, _after = prompt.split("User", "document", name="{{document}}")
    assert before == "Hello {{document}}.\n"


def test_missing_file_is_a_prompt_error(tmp_path: Path) -> None:
    with pytest.raises(PromptError, match="not found"):
        load_prompt("demo", 9, directory=tmp_path)


def test_unknown_section_cannot_be_rendered() -> None:
    prompt = parse_prompt("demo", 1, GOOD)
    with pytest.raises(PromptError, match="no section"):
        prompt.render("Retry")


def test_the_committed_extract_po_prompt_loads() -> None:
    prompt = load_prompt("extract_po", 1)
    assert prompt.prompt_version == "extract_po/v1"
    assert (PROMPTS_DIR / "extract_po" / "v1.md").is_file()
    assert {"System", "User", "Retry"} <= set(prompt.sections)

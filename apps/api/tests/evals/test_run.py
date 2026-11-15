"""The M1 PR 0b eval runner: every outcome fails, each with its own exit code (owner rule)."""

import itertools
import socket
import sys
from pathlib import Path

import pytest

from evals import run

# Planted in fixture files: no message may ever echo document content.
_DOC_MARKER = "DOC-CONTENT-MARKER-7f3a"
_KEY_VALUE = "sk-test-not-a-real-key-91c2"

_EXIT_CODES = {
    name: value
    for name, value in vars(run).items()
    if name.startswith("EXIT_") and isinstance(value, int)
}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No real key, no network, and recordings/baseline in tmp (never the repo's)."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setattr(run, "RECORDINGS_DIR", tmp_path / "recordings")
    monkeypatch.setattr(run, "BASELINE_PATH", tmp_path / "baseline.json")

    def _no_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the eval runner must not open sockets in PR 0b")

    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)


def _case(root: Path, name: str, *, source: bool = True, expected: bool = True) -> Path:
    case = root / name
    case.mkdir(parents=True)
    if source:
        (case / "source.pdf").write_text(f"PO body {_DOC_MARKER}", encoding="utf-8")
    if expected:
        (case / "expected.json").write_text(f'{{"note": "{_DOC_MARKER}"}}', encoding="utf-8")
    return case


def _record(name: str) -> None:
    run.RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    (run.RECORDINGS_DIR / f"{name}.json").write_text(_DOC_MARKER, encoding="utf-8")


def _baseline() -> None:
    run.BASELINE_PATH.write_text("{}", encoding="utf-8")


def _main(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    code = run.main(list(argv))
    out, err = capsys.readouterr()
    assert out == "", "the runner writes only to stderr"
    return code, err


def _assert_one_line(err: str) -> None:
    assert err.endswith("\n")
    assert err.count("\n") == 1, err
    assert _DOC_MARKER not in err
    assert _KEY_VALUE not in err


# --- constants -----------------------------------------------------------------------------


def test_fixtures_dir_is_the_packages_fixtures() -> None:
    assert Path(run.__file__).resolve().parent / "fixtures" == run.FIXTURES_DIR


def test_module_paths_point_into_the_evals_package() -> None:
    # RECORDINGS_DIR/BASELINE_PATH are patched per test (autouse), so check the source.
    package = Path(run.__file__).resolve().parent
    source = (package / "run.py").read_text(encoding="utf-8")
    assert 'FIXTURES_DIR: Final = _PACKAGE / "fixtures"' in source
    assert 'RECORDINGS_DIR: Final = _PACKAGE / "recordings"' in source
    # ci.yml's `baseline` step checks this exact file; the two must agree.
    assert 'BASELINE_PATH: Final = _PACKAGE / "baseline.json"' in source
    assert package == Path(__file__).resolve().parents[2] / "evals"


def test_exit_codes_are_distinct_and_never_zero() -> None:
    assert set(_EXIT_CODES) == {
        "EXIT_USAGE",
        "EXIT_LIVE_NO_KEY",
        "EXIT_LIVE_NOT_IMPLEMENTED",
        "EXIT_NO_FIXTURES",
        "EXIT_INVALID_FIXTURE",
        "EXIT_MISSING_RECORDINGS",
        "EXIT_NO_BASELINE",
        "EXIT_SCORING_NOT_IMPLEMENTED",
    }
    assert 0 not in _EXIT_CODES.values()
    assert len(set(_EXIT_CODES.values())) == len(_EXIT_CODES)


# --- arguments -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--fail-on-regression"],
        ["--replay", "--live"],
        ["--replay", "--unknown"],
        ["--help"],
        ["-h"],
        ["--replay", "--help"],
    ],
)
def test_usage_errors_and_help_exit_with_the_usage_code(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    code = run.main(argv)
    capsys.readouterr()

    assert code == run.EXIT_USAGE


# --- live mode -----------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "   "])
def test_live_without_a_key_fails(
    value: str | None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    if value is not None:
        monkeypatch.setenv("LLM_API_KEY", value)

    code, err = _main(capsys, "--live")

    assert code == run.EXIT_LIVE_NO_KEY
    assert "LLM_API_KEY" in err
    _assert_one_line(err)


def test_live_with_a_key_is_not_implemented_and_calls_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("LLM_API_KEY", _KEY_VALUE)
    fixtures = tmp_path / "fixtures"
    _case(fixtures, "clean_pdf")
    before = set(sys.modules)

    code, err = _main(capsys, "--live", "--fixtures", str(fixtures))

    assert code == run.EXIT_LIVE_NOT_IMPLEMENTED
    assert "live mode not implemented until PR 9" in err
    _assert_one_line(err)
    imported = set(sys.modules) - before
    assert not {m for m in imported if m.split(".")[0] in {"anthropic", "openai", "httpx"}}


def test_live_checks_come_before_fixture_checks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # No fixtures at all, but the missing key is reported first.
    code, _ = _main(capsys, "--live", "--fixtures", str(tmp_path / "absent"))

    assert code == run.EXIT_LIVE_NO_KEY


# --- replay mode ---------------------------------------------------------------------------


def test_no_fixtures_dir_fails(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code, err = _main(capsys, "--replay", "--fixtures", str(tmp_path / "absent"))

    assert code == run.EXIT_NO_FIXTURES
    assert "no fixtures" in err
    _assert_one_line(err)


def test_empty_fixtures_dir_fails(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / ".gitkeep").write_text("", encoding="utf-8")
    (fixtures / ".hidden").mkdir()

    code, err = _main(capsys, "--replay", "--fail-on-regression", "--fixtures", str(fixtures))

    assert code == run.EXIT_NO_FIXTURES
    _assert_one_line(err)


def test_the_committed_fixtures_dir_has_no_cases_yet(capsys: pytest.CaptureFixture[str]) -> None:
    # Exactly what CI sees until PR 6 adds fixtures (default --fixtures).
    code, err = _main(capsys, "--replay", "--fail-on-regression")

    assert code == run.EXIT_NO_FIXTURES
    assert "no fixtures" in err


def test_private_fixtures_are_skipped_by_default(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    fixtures = tmp_path / "fixtures"
    _case(fixtures / "private", "acme_real_po")

    code, err = _main(capsys, "--replay", "--fixtures", str(fixtures))

    assert code == run.EXIT_NO_FIXTURES
    assert "acme_real_po" not in err


def test_private_fixtures_count_with_include_private_and_names_stay_hidden(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    fixtures = tmp_path / "fixtures"
    _case(fixtures / "private", "acme_real_po")
    _case(fixtures, "clean_pdf")

    code, err = _main(capsys, "--replay", "--include-private", "--fixtures", str(fixtures))

    assert code == run.EXIT_MISSING_RECORDINGS
    assert "clean_pdf" in err
    assert "acme_real_po" not in err
    assert "1 private" in err
    _assert_one_line(err)


@pytest.mark.parametrize(("source", "expected"), [(False, True), (True, False), (False, False)])
def test_incomplete_case_fails(
    source: bool, expected: bool, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    fixtures = tmp_path / "fixtures"
    _case(fixtures, "good_case")
    _case(fixtures, "broken_case", source=source, expected=expected)

    code, err = _main(capsys, "--replay", "--fixtures", str(fixtures))

    assert code == run.EXIT_INVALID_FIXTURE
    assert "broken_case" in err
    assert "good_case" not in err
    _assert_one_line(err)


def test_two_source_files_make_a_case_invalid(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    case = _case(tmp_path / "fixtures", "two_sources")
    (case / "source.png").write_bytes(b"\x89PNG")

    code, _ = _main(capsys, "--replay", "--fixtures", str(tmp_path / "fixtures"))

    assert code == run.EXIT_INVALID_FIXTURE


def test_a_directory_named_like_the_files_does_not_count(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    case = _case(tmp_path / "fixtures", "dirs_only", source=False, expected=False)
    (case / "source.pdf").mkdir()
    (case / "expected.json").mkdir()

    code, _ = _main(capsys, "--replay", "--fixtures", str(tmp_path / "fixtures"))

    assert code == run.EXIT_INVALID_FIXTURE


def test_unsafe_case_names_are_invalid_and_not_echoed(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    fixtures = tmp_path / "fixtures"
    _case(fixtures, "Customer Name PO#1")

    code, err = _main(capsys, "--replay", "--fixtures", str(fixtures))

    assert code == run.EXIT_INVALID_FIXTURE
    assert "Customer" not in err
    assert "1 with an unsafe name" in err
    _assert_one_line(err)


def test_missing_recordings_fail_and_list_the_cases(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    fixtures = tmp_path / "fixtures"
    for name in ("b_case", "a_case", "c_case"):
        _case(fixtures, name)
    _record("c_case")
    _baseline()

    code, err = _main(capsys, "--replay", "--fail-on-regression", "--fixtures", str(fixtures))

    assert code == run.EXIT_MISSING_RECORDINGS
    assert "a_case, b_case" in err
    assert "c_case" not in err
    _assert_one_line(err)


def test_recordings_in_subdirectories_are_found(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    fixtures = tmp_path / "fixtures"
    _case(fixtures, "clean_pdf")
    nested = run.RECORDINGS_DIR / "extract_po.v1" / "model"
    nested.mkdir(parents=True)
    (nested / "clean_pdf.json").write_text("{}", encoding="utf-8")

    code, _ = _main(capsys, "--replay", "--fixtures", str(fixtures))

    assert code == run.EXIT_NO_BASELINE


def test_no_baseline_fails(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    _case(fixtures, "clean_pdf")
    _record("clean_pdf")

    code, err = _main(capsys, "--replay", "--fail-on-regression", "--fixtures", str(fixtures))

    assert code == run.EXIT_NO_BASELINE
    assert "baseline" in err
    _assert_one_line(err)


def test_everything_present_still_fails_until_scoring_exists(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    fixtures = tmp_path / "fixtures"
    _case(fixtures, "clean_pdf")
    _record("clean_pdf")
    _baseline()

    code, err = _main(capsys, "--replay", "--fail-on-regression", "--fixtures", str(fixtures))

    assert code == run.EXIT_SCORING_NOT_IMPLEMENTED
    assert "scoring not implemented (PR 7)" in err
    _assert_one_line(err)


def test_the_runner_never_reads_fixture_or_recording_contents(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    fixtures = tmp_path / "fixtures"
    _case(fixtures, "clean_pdf")
    _record("clean_pdf")
    _baseline()

    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("PR 0b checks presence only")

    monkeypatch.setattr(Path, "read_text", _refuse)
    monkeypatch.setattr(Path, "read_bytes", _refuse)
    monkeypatch.setattr(Path, "open", _refuse)

    code, _ = _main(capsys, "--replay", "--fixtures", str(fixtures))

    assert code == run.EXIT_SCORING_NOT_IMPLEMENTED


# --- the owner rule: no path returns 0 -----------------------------------------------------


_KEYS = [None, "", " ", _KEY_VALUE]
_FIXTURE_STATES = ["absent_dir", "empty", "private_only", "incomplete", "complete"]


def _build_fixtures(root: Path, state: str) -> Path:
    fixtures = root / "fixtures"
    if state == "absent_dir":
        return fixtures
    fixtures.mkdir()
    if state == "private_only":
        _case(fixtures / "private", "real_po")
    elif state == "incomplete":
        _case(fixtures, "clean_pdf")
        _case(fixtures, "no_expected", expected=False)
    elif state == "complete":
        _case(fixtures, "clean_pdf")
        _case(fixtures / "private", "real_po")
    return fixtures


def test_no_combination_of_conditions_returns_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # Every combination, in one test: each filesystem layout is built once (Windows file
    # creation dominates), then every mode / key / flag combination runs against it.
    layouts = itertools.product(_FIXTURE_STATES, [False, True], [False, True])
    flags = list(
        itertools.product(
            ["--replay", "--live"],
            _KEYS,
            [False, True],  # --include-private
            [False, True],  # --fail-on-regression
        )
    )
    seen: set[int] = set()
    for i, (state, recorded, baseline) in enumerate(layouts):
        root = tmp_path / str(i)
        root.mkdir()
        monkeypatch.setattr(run, "RECORDINGS_DIR", root / "recordings")
        monkeypatch.setattr(run, "BASELINE_PATH", root / "baseline.json")
        fixtures = _build_fixtures(root, state)
        if recorded:
            for name in ("clean_pdf", "no_expected", "real_po"):
                _record(name)
        if baseline:
            _baseline()
        for mode, key, private, regression in flags:
            if key is None:
                monkeypatch.delenv("LLM_API_KEY", raising=False)
            else:
                monkeypatch.setenv("LLM_API_KEY", key)
            argv = [mode, "--fixtures", str(fixtures)]
            argv += ["--include-private"] if private else []
            argv += ["--fail-on-regression"] if regression else []

            code, err = _main(capsys, *argv)

            context = (state, recorded, baseline, argv, key is not None)
            assert code != 0, context
            assert code in _EXIT_CODES.values(), context
            _assert_one_line(err)
            seen.add(code)

    # The loop really reached every runtime outcome (usage is covered above).
    assert seen == set(_EXIT_CODES.values()) - {run.EXIT_USAGE}


def test_running_as_a_module_exits_non_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["evals.run", "--replay", "--fail-on-regression"])

    with pytest.raises(SystemExit) as exc:
        run.run_cli()

    assert exc.value.code == run.EXIT_NO_FIXTURES

"""The eval runner: every outcome fails, each with its own exit code (owner rule, M1 PR 0b).

Since PR 6 the committed fixtures exist, so CI stops at "missing recordings" (22) until
PR 9 commits recordings and the baseline.
"""

import builtins
import io
import itertools
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from evals import run

_API_DIR = Path(__file__).resolve().parents[2]
_REPO_ROOT = _API_DIR.parents[1]

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


def _record(name: str, *, private: bool = False) -> None:
    folder = run.RECORDINGS_DIR / "private" if private else run.RECORDINGS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.json").write_text(_DOC_MARKER, encoding="utf-8")


def _symlink(link: Path, target: Path, *, is_dir: bool) -> None:
    """Skips only where the OS refuses symlinks (Windows without the privilege)."""
    try:
        link.symlink_to(target, target_is_directory=is_dir)
    except OSError as exc:
        if sys.platform == "win32":
            pytest.skip(f"this Windows account cannot create symlinks ({exc.winerror})")
        raise


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


def _committed_cases() -> list[str]:
    return sorted(
        p.name
        for p in run.FIXTURES_DIR.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name != "private"
    )


def _committed_line() -> str:
    names = _committed_cases()
    return f"evals: {len(names)} case(s) without a recording: {', '.join(names)}\n"


def test_the_committed_fixtures_have_cases_and_no_recordings_yet(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # PR 6 handover (docs/plans/m1.md): the committed fixtures are the generator's
    # registry, every case is valid, and with no recording (PR 9) CI stops at 22.
    from evals.generate.cases import by_name

    assert _committed_cases() == sorted(by_name())
    code, err = _main(capsys, "--replay", "--fail-on-regression")

    assert code == run.EXIT_MISSING_RECORDINGS
    assert err == _committed_line()


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

    attempts: list[str] = []

    def _guard(real: object) -> object:
        def guarded(file: object, *args: object, **kwargs: object) -> object:
            path = file if isinstance(file, str | bytes | os.PathLike) else None
            if path is not None and Path(os.fsdecode(path)).resolve().is_relative_to(tmp_path):
                attempts.append("open")
                raise AssertionError("PR 0b checks presence only")
            return real(file, *args, **kwargs)  # type: ignore[operator]

        return guarded

    monkeypatch.setattr(Path, "read_text", _refuse)
    monkeypatch.setattr(Path, "read_bytes", _refuse)
    monkeypatch.setattr(Path, "open", _refuse)
    monkeypatch.setattr(builtins, "open", _guard(builtins.open))
    monkeypatch.setattr(io, "open", _guard(io.open))
    monkeypatch.setattr(os, "open", _guard(os.open))

    code, _ = _main(capsys, "--replay", "--fixtures", str(fixtures))

    assert code == run.EXIT_SCORING_NOT_IMPLEMENTED
    assert attempts == []


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
            for name in ("clean_pdf", "no_expected"):
                _record(name)
            _record("real_po", private=True)
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

    assert exc.value.code == run.EXIT_MISSING_RECORDINGS


# --- the real entry point, exactly as CI runs it (sec M1) ----------------------------------


def _entry_point(*argv: str) -> subprocess.CompletedProcess[str]:
    # CI: `uv run python -m evals.run ...` in apps/api, no -I, no LLM_API_KEY. The real
    # recordings directory doesn't exist until PR 9, so the committed fixtures stop at
    # "missing recordings" (22), before the baseline check (asserted here, from the source
    # path: RECORDINGS_DIR itself is patched per test).
    assert not (Path(run.__file__).resolve().parent / "recordings").exists(), (
        "PR 9 must replace this test: recordings exist now"
    )
    env = {k: v for k, v in os.environ.items() if k not in {"LLM_API_KEY", "PYTHONSAFEPATH"}}
    return subprocess.run(  # noqa: S603 - this interpreter, fixed arguments
        [sys.executable, "-m", "evals.run", *argv],
        cwd=_API_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=50,
        check=False,
    )


def test_ci_command_exits_missing_recordings() -> None:
    proc = _entry_point("--replay", "--fail-on-regression")

    assert proc.returncode == run.EXIT_MISSING_RECORDINGS, proc.stderr
    assert proc.stdout == ""
    assert proc.stderr == _committed_line()


def test_help_through_the_entry_point_exits_usage() -> None:
    proc = _entry_point("--help")

    assert proc.returncode == run.EXIT_USAGE
    assert "--replay" in proc.stdout


# --- private recordings and the ignore rules (sec M2, L2) ----------------------------------


def test_private_fixtures_and_recordings_are_gitignored() -> None:
    git = shutil.which("git")
    assert git, "git is needed (CI's checkout has it)"
    paths = ["apps/api/evals/fixtures/private/x", "apps/api/evals/recordings/private/x"]
    proc = subprocess.run(  # noqa: S603 - git found on PATH, fixed arguments
        [git, "check-ignore", "--no-index", "-v", *paths],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    ignored = {line.split("\t", 1)[1] for line in proc.stdout.splitlines()}
    assert ignored == set(paths)


def test_a_private_case_needs_its_recording_under_recordings_private(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    fixtures = tmp_path / "fixtures"
    _case(fixtures / "private", "real_po")
    _record("real_po")  # public location: does not count for a private case
    _baseline()

    code, err = _main(capsys, "--replay", "--include-private", "--fixtures", str(fixtures))

    assert code == run.EXIT_MISSING_RECORDINGS
    assert "1 private" in err
    assert "real_po" not in err

    _record("real_po", private=True)
    code, _ = _main(capsys, "--replay", "--include-private", "--fixtures", str(fixtures))

    assert code == run.EXIT_SCORING_NOT_IMPLEMENTED


def test_a_public_case_ignores_recordings_under_recordings_private(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    fixtures = tmp_path / "fixtures"
    _case(fixtures, "clean_pdf")
    _record("clean_pdf", private=True)
    nested = run.RECORDINGS_DIR / "Private" / "deeper"  # any case of the folder name
    nested.mkdir(parents=True)
    (nested / "clean_pdf.json").write_text("{}", encoding="utf-8")

    code, err = _main(capsys, "--replay", "--fixtures", str(fixtures))

    assert code == run.EXIT_MISSING_RECORDINGS
    assert "clean_pdf" in err


@pytest.mark.parametrize("parts", [("Private",), ("private", "sub"), ("x", "PRIVATE", "y")])
def test_a_fixtures_path_through_a_private_folder_hides_names(
    parts: tuple[str, ...], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    fixtures = tmp_path.joinpath(*parts)
    _case(fixtures, "customer_po")

    code, err = _main(capsys, "--replay", "--fixtures", str(fixtures))

    assert code == run.EXIT_MISSING_RECORDINGS
    assert "customer_po" not in err
    assert "1 private" in err
    _assert_one_line(err)

    _record("customer_po", private=True)
    code, _ = _main(capsys, "--replay", "--fixtures", str(fixtures))

    assert code == run.EXIT_NO_BASELINE


def test_a_path_resolving_into_the_private_fixtures_hides_names(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    fixtures_dir = tmp_path / "fixtures"
    monkeypatch.setattr(run, "FIXTURES_DIR", fixtures_dir)
    _case(fixtures_dir / "private", "customer_po")
    alias = tmp_path / "alias"
    _symlink(alias, fixtures_dir / "private", is_dir=True)

    code, err = _main(capsys, "--replay", "--fixtures", str(alias))

    assert code == run.EXIT_MISSING_RECORDINGS
    assert "customer_po" not in err
    assert "1 private" in err


# --- exact recording match (sec L1) --------------------------------------------------------


def test_recordings_match_the_case_name_exactly(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    fixtures = tmp_path / "fixtures"
    _case(fixtures, "clean")
    _case(fixtures, "clean_pdf")
    _record("clean_pdf")

    code, err = _main(capsys, "--replay", "--fixtures", str(fixtures))

    assert code == run.EXIT_MISSING_RECORDINGS
    assert err == "evals: 1 case(s) without a recording: clean\n"


# --- symlinks (sec L3) ---------------------------------------------------------------------


def test_a_symlinked_case_directory_is_skipped(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    outside = _case(tmp_path / "elsewhere", "outside_case")
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _symlink(fixtures / "linked_case", outside, is_dir=True)

    code, err = _main(capsys, "--replay", "--fixtures", str(fixtures))

    assert code == run.EXIT_NO_FIXTURES
    assert "outside" not in err


@pytest.mark.parametrize("name", ["source.pdf", "expected.json"])
def test_a_file_symlinked_outside_the_fixtures_is_invalid(
    name: str, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    target = tmp_path / "secret_target_dir" / name
    target.parent.mkdir()
    target.write_text(_DOC_MARKER, encoding="utf-8")
    fixtures = tmp_path / "fixtures"
    case = _case(
        fixtures, "linked_file", source=name != "source.pdf", expected=name != "expected.json"
    )
    _symlink(case / name, target, is_dir=False)

    code, err = _main(capsys, "--replay", "--fixtures", str(fixtures))

    assert code == run.EXIT_INVALID_FIXTURE
    assert "linked_file" in err
    assert "secret_target_dir" not in err
    _assert_one_line(err)


def test_a_private_folder_symlinked_outside_the_fixtures_is_invalid(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _case(tmp_path / "secret_target_dir", "real_po")
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _symlink(fixtures / "private", tmp_path / "secret_target_dir", is_dir=True)

    code, err = _main(capsys, "--replay", "--include-private", "--fixtures", str(fixtures))

    assert code == run.EXIT_INVALID_FIXTURE
    assert "secret_target_dir" not in err
    assert "real_po" not in err
    assert "1 private" in err


def test_a_symlink_inside_the_fixtures_root_is_allowed(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    fixtures = tmp_path / "fixtures"
    first = _case(fixtures, "first")
    second = _case(fixtures, "second", source=False)
    _symlink(second / "source.pdf", first / "source.pdf", is_dir=False)

    code, _ = _main(capsys, "--replay", "--fixtures", str(fixtures))

    assert code == run.EXIT_MISSING_RECORDINGS

import os
import subprocess
import sys
import time
from typing import Any

import pytest
import structlog

from app.extraction.inputs import ParsedDocument, isolate, parse_document
from app.extraction.inputs.errors import InputError
from app.extraction.inputs.isolate import parse_isolated
from app.extraction.inputs.limits import MAX_FILE_BYTES
from tests.extraction.inputs import targets
from tests.extraction.inputs.files import make_email, make_image, make_pdf


def run(data: bytes, parser: str, timeout: float = 30) -> ParsedDocument:
    return isolate._parse_isolated(data, timeout, parser)


def code_of(data: bytes, parser: str, timeout: float = 30) -> str:
    with pytest.raises(InputError) as caught:
        run(data, parser, timeout)
    return caught.value.code


@pytest.mark.parametrize(
    "parser", ["os:system", "subprocess:run", "app.extraction.inputs", "tests:x; import os"]
)
def test_only_app_or_test_parsers_can_be_run(parser: str) -> None:
    # `sec` delta L-8.
    with pytest.raises(ValueError, match="parser"):
        run(b"x", parser)


def test_the_child_path_has_no_current_directory() -> None:
    # `sec` delta L-9: `os.defpath` on Windows starts with ".".
    entries = isolate.child_environment()["PATH"].split(os.pathsep)
    assert "." not in entries
    assert "" not in entries


def test_a_child_process_parses_like_the_parent() -> None:
    for data in (
        make_pdf(["PO# 4500012345 00123-A Widget 12 CS"]),
        make_image(fmt="JPEG"),
        make_email(attachment=b"%PDF-1.4"),
    ):
        assert parse_isolated(data) == parse_document(data)


def test_a_rejection_comes_back_as_its_code() -> None:
    assert code_of(b"GIF89a", isolate.DEFAULT_PARSER) == "unsupported_type"


def test_the_child_gets_no_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    # `sec` M1: a spawn child inherited DATABASE_URL and LLM_API_KEY.
    monkeypatch.setenv("DATABASE_URL", "postgresql://po_worker:secret@db.example/postgres")
    monkeypatch.setenv("LLM_API_KEY", "sk-ant-secret")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_x")
    document = run(b"x", targets.name("environment"))
    names = set((document.text or "").split())
    assert not names & {"DATABASE_URL", "LLM_API_KEY", "SUPABASE_SECRET_KEY"}
    # LC_CTYPE: Python's C-locale coercion adds it on Linux (`sec` delta L-6).
    assert names <= {"PATH", "SYSTEMROOT", "LC_CTYPE"}


def test_the_child_receives_the_whole_file() -> None:
    data = b"%" * (3 * 1024 * 1024 + 7)  # bigger than a pipe buffer
    assert run(data, targets.name("echo_size")).text == str(len(data))


def test_a_file_over_the_cap_is_refused_without_a_child(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_child(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a child was started")

    monkeypatch.setattr(subprocess, "Popen", no_child)
    with pytest.raises(InputError, match="file_too_large"):
        parse_isolated(b"\0" * (MAX_FILE_BYTES + 1))


def test_a_hung_parser_is_killed_at_the_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[subprocess.Popen[bytes]] = []
    real_popen = subprocess.Popen

    def recording_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        process = real_popen(*args, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    start = time.perf_counter()
    assert code_of(b"x", targets.name("hang"), timeout=3) == "parse_timeout"
    assert time.perf_counter() - start < 15
    (process,) = started
    assert process.poll() is not None  # killed and reaped, not left running


def test_a_child_that_closes_stdout_and_lingers_times_out() -> None:
    # The parent waits on the process, not on its stdout: a closed pipe isn't an answer.
    start = time.perf_counter()
    assert code_of(b"x", targets.name("close_and_linger"), timeout=3) == "parse_timeout"
    assert time.perf_counter() - start < 15


def _gone(pid: int) -> bool:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def test_a_grandchild_holding_the_pipe_cannot_stall_the_parent() -> None:
    # `sec` delta M-B: a grandchild kept stdout open and outlived the kill.
    start = time.perf_counter()
    try:
        document = run(b"x", targets.name("spawn_and_exit"), timeout=20)
    except InputError as error:
        # Linux (not root): the child can't start a process. Windows: the grandchild
        # (no process group there) still holds the pipe, so the reply is unknown.
        assert error.code == "parse_failed"
    else:
        # POSIX as root (RLIMIT_NPROC doesn't apply): it started, and died with the group.
        assert sys.platform != "win32"
        assert not (sys.platform == "linux" and os.geteuid() != 0), "the child started one"
        pid = int((document.text or "").split()[1])
        assert _gone(pid)
    assert time.perf_counter() - start < 15


@pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() == 0,
    reason="Linux /proc; root reads any process's environ",
)
def test_the_child_cannot_read_the_parents_environ() -> None:
    # `sec` delta M-A: same user, so /proc/<ppid>/environ (DATABASE_URL, ...) was readable.
    assert run(b"x", targets.name("read_parent_environ")).text == "denied"


@pytest.mark.parametrize("target", ["die", "raise_secret", "wrong_answer", "junk_reply", "flood"])
def test_a_failed_or_compromised_child_is_parse_failed(
    target: str, capfd: pytest.CaptureFixture[str]
) -> None:
    assert code_of(b"x", targets.name(target)) == "parse_failed"
    out, err = capfd.readouterr()
    assert "SECRET-IN-TRACEBACK" not in out + err
    assert "pwned" not in out + err


@pytest.mark.skipif(sys.platform != "linux", reason="the memory limit is Linux-only")
def test_the_child_memory_limit_stops_a_bomb() -> None:
    assert code_of(b"x", targets.name("allocate")) == "parse_failed"


def test_the_parent_logs_the_outcome() -> None:
    with structlog.testing.capture_logs() as logs:
        parse_isolated(make_email(attachment=b"%PDF-1.4"))
    assert [entry["event"] for entry in logs] == ["document.parsed"]
    assert logs[0]["warnings"] == ["attachments_ignored"]

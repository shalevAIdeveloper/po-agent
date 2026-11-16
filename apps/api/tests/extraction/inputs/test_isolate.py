import subprocess
import sys
import time
from typing import Any

import pytest
import structlog

from app.extraction.inputs import isolate, parse_document
from app.extraction.inputs.errors import InputError
from app.extraction.inputs.isolate import parse_isolated
from app.extraction.inputs.limits import MAX_FILE_BYTES
from tests.extraction.inputs import targets
from tests.extraction.inputs.files import make_email, make_image, make_pdf


def code_of(data: bytes, parser: str, timeout: float = 30) -> str:
    with pytest.raises(InputError) as caught:
        parse_isolated(data, timeout=timeout, parser=parser)
    return caught.value.code


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
    document = parse_isolated(b"x", parser=targets.name("environment"))
    names = set((document.text or "").split())
    assert not names & {"DATABASE_URL", "LLM_API_KEY", "SUPABASE_SECRET_KEY"}
    # LC_CTYPE: Python's C-locale coercion adds it on Linux (`sec` delta L-6).
    assert names <= {"PATH", "SYSTEMROOT", "LC_CTYPE"}


def test_the_child_receives_the_whole_file() -> None:
    data = b"%" * (3 * 1024 * 1024 + 7)  # bigger than a pipe buffer
    assert parse_isolated(data, parser=targets.name("echo_size")).text == str(len(data))


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


def test_a_child_that_closes_stdout_and_lingers_is_killed() -> None:
    start = time.perf_counter()
    assert code_of(b"x", targets.name("close_and_linger"), timeout=20) == "parse_failed"
    assert time.perf_counter() - start < 15


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

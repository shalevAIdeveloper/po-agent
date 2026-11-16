import sys
import time

import pytest
import structlog

from app.extraction.inputs import parse_document, run_parsers
from app.extraction.inputs.errors import InputError
from app.extraction.inputs.isolate import Parser, parse_isolated
from tests.extraction.inputs import targets
from tests.extraction.inputs.files import make_email, make_pdf


def code_of(data: bytes, parser: Parser, timeout: float = 30) -> str:
    with pytest.raises(InputError) as caught:
        parse_isolated(data, timeout=timeout, parser=parser)
    return caught.value.code


def test_a_child_process_parses_like_the_parent() -> None:
    data = make_pdf(["PO# 4500012345 00123-A Widget 12 CS"])
    assert parse_isolated(data) == parse_document(data)


def test_a_rejection_comes_back_as_its_code() -> None:
    assert code_of(b"GIF89a", run_parsers) == "unsupported_type"


def test_a_hung_parser_is_killed_at_the_timeout() -> None:
    start = time.perf_counter()
    assert code_of(b"x", targets.hang, timeout=2) == "parse_timeout"
    assert time.perf_counter() - start < 15


def test_a_crashed_child_is_parse_failed() -> None:
    assert code_of(b"x", targets.die) == "parse_failed"


def test_an_exception_in_the_child_prints_no_traceback(capfd: pytest.CaptureFixture[str]) -> None:
    assert code_of(b"x", targets.raise_secret) == "parse_failed"
    out, err = capfd.readouterr()
    assert "SECRET-IN-TRACEBACK" not in out + err


def test_an_answer_of_the_wrong_type_is_parse_failed() -> None:
    assert code_of(b"x", targets.wrong_answer) == "parse_failed"


@pytest.mark.skipif(sys.platform != "linux", reason="the memory limit is Linux-only")
def test_the_child_memory_limit_stops_a_bomb() -> None:
    assert code_of(b"x", targets.allocate) == "parse_failed"


def test_the_parent_logs_the_outcome() -> None:
    with structlog.testing.capture_logs() as logs:
        parse_isolated(make_email(attachment=b"%PDF-1.4"))
    assert [entry["event"] for entry in logs] == ["document.parsed"]
    assert logs[0]["warnings"] == ["attachments_ignored"]

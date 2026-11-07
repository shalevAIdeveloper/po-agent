import io
import json
import logging
from typing import Any

import structlog
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.logging import REDACTED, configure_logging
from app.main import create_app


def _lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


def test_log_lines_are_json() -> None:
    stream = io.StringIO()
    configure_logging(stream=stream)

    structlog.get_logger().info("something.happened", count=3)

    [line] = _lines(stream)
    assert line["event"] == "something.happened"
    assert line["count"] == 3
    assert line["level"] == "info"
    assert "timestamp" in line


def test_sensitive_keys_are_redacted() -> None:
    stream = io.StringIO()
    configure_logging(stream=stream)

    structlog.get_logger().info("auth.attempt", Authorization="Bearer abc.def", password="hunter2")

    output = stream.getvalue()
    assert "abc.def" not in output
    assert "hunter2" not in output
    [line] = _lines(stream)
    assert line["Authorization"] == REDACTED


def test_level_filter_drops_debug() -> None:
    stream = io.StringIO()
    configure_logging(level="INFO", stream=stream)

    structlog.get_logger().debug("noisy")

    assert stream.getvalue() == ""


PO_TEXT = "ACME 123 Main St $4.50"


def test_exception_message_is_not_logged() -> None:
    stream = io.StringIO()
    configure_logging(stream=stream)

    try:
        raise ValueError(PO_TEXT)
    except ValueError:
        structlog.get_logger().exception("extraction.failed")

    assert PO_TEXT not in stream.getvalue()
    [line] = _lines(stream)
    assert line["exc_type"] == "builtins.ValueError"
    assert line["exc_frames"]
    assert "exception" not in line


def test_pydantic_validation_input_is_not_logged() -> None:
    class Line(BaseModel):
        qty: int

    stream = io.StringIO()
    configure_logging(stream=stream)

    try:
        Line.model_validate({"qty": PO_TEXT})
    except ValidationError:
        structlog.get_logger().exception("extraction.invalid_output")

    assert PO_TEXT not in stream.getvalue()
    [line] = _lines(stream)
    assert line["exc_type"] == "pydantic_core._pydantic_core.ValidationError"


def test_chained_cause_type_is_logged_without_message() -> None:
    stream = io.StringIO()
    configure_logging(stream=stream)

    try:
        try:
            raise KeyError(PO_TEXT)
        except KeyError as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError:
        structlog.get_logger().exception("job.failed")

    assert PO_TEXT not in stream.getvalue()
    [line] = _lines(stream)
    assert line["exc_cause_type"] == "builtins.KeyError"


def test_stdlib_loggers_are_json_and_sanitized() -> None:
    stream = io.StringIO()
    configure_logging(stream=stream)
    stdlib_logger = logging.getLogger("sqlalchemy.engine")

    stdlib_logger.warning("pool exhausted")
    try:
        raise ValueError(PO_TEXT)
    except ValueError:
        stdlib_logger.exception("statement failed")

    assert PO_TEXT not in stream.getvalue()
    warning, error = _lines(stream)
    assert warning == warning | {"event": "pool exhausted", "level": "warning"}
    assert warning["logger"] == "sqlalchemy.engine"
    assert error["exc_type"] == "builtins.ValueError"


def test_uvicorn_access_log_is_dropped() -> None:
    stream = io.StringIO()
    configure_logging(stream=stream)

    # Same shape uvicorn uses; the query string carries PII.
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d',
        "127.0.0.1:5000",
        "GET",
        "/health?email=someone@example.com",
        "1.1",
        200,
    )

    assert stream.getvalue() == ""


def test_uvicorn_error_log_goes_through_structlog() -> None:
    uvicorn_error = logging.getLogger("uvicorn.error")
    # Simulate uvicorn's own dictConfig having attached a plain-text handler.
    uvicorn_error.addHandler(logging.StreamHandler(io.StringIO()))
    uvicorn_error.propagate = False
    stream = io.StringIO()
    configure_logging(stream=stream)

    uvicorn_error.info("Started server process [%d]", 1234)

    [line] = _lines(stream)
    assert line["event"] == "Started server process [1234]"
    assert line["logger"] == "uvicorn.error"


def test_http_client_urls_are_not_logged_at_info() -> None:
    stream = io.StringIO()
    configure_logging(stream=stream)

    logging.getLogger("httpx").info("HTTP Request: GET https://x.test/o?token=abc")

    assert stream.getvalue() == ""


def test_unhandled_route_error_is_logged_without_message(settings: Settings) -> None:
    app = create_app(settings)

    async def boom() -> None:
        raise RuntimeError(PO_TEXT)

    app.add_api_route("/boom", boom)
    stream = io.StringIO()
    configure_logging(stream=stream)

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/boom").status_code == 500

    assert PO_TEXT not in stream.getvalue()
    [line] = [entry for entry in _lines(stream) if entry["event"] == "http.request_failed"]
    assert line["exc_type"] == "builtins.RuntimeError"


def test_request_log_has_request_id_and_no_token(settings: Settings) -> None:
    app = create_app(settings)
    stream = io.StringIO()
    configure_logging(stream=stream)

    with TestClient(app) as client:
        client.get(
            "/health?email=someone@example.com",
            headers={"Authorization": "Bearer secret-token", "x-request-id": "req-1"},
        )

    output = stream.getvalue()
    assert "secret-token" not in output
    assert "someone@example.com" not in output
    [line] = [entry for entry in _lines(stream) if entry["event"] == "http.request"]
    assert line["request_id"] == "req-1"
    assert line["path"] == "/health"
    assert line["status"] == 200

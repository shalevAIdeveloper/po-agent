import io
import json
from typing import Any

import structlog
from fastapi.testclient import TestClient

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

import logging
import sys
from collections.abc import MutableMapping
from typing import Any, TextIO

import structlog

REDACTED = "[REDACTED]"

# Keys whose values must never reach logs (credentials, tokens, cookies).
SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "password",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "secret",
        "database_url",
    }
)


def redact_sensitive(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in event_dict:
        if key.lower() in SENSITIVE_KEYS:
            event_dict[key] = REDACTED
    return event_dict


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
    """One JSON object per line on stdout (or `stream`)."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_sensitive,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        logger_factory=structlog.PrintLoggerFactory(file=stream or sys.stdout),
        cache_logger_on_first_use=False,
    )

import logging
import sys
import traceback
from collections.abc import MutableMapping
from types import TracebackType
from typing import Any, TextIO

import structlog

REDACTED = "[REDACTED]"
HANDLER_NAME = "po-agent"
MAX_EXC_FRAMES = 20

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

# uvicorn's own loggers; access logs are dropped because they include query strings
# and request_context already logs every request without them.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error")
_UVICORN_ACCESS_LOGGER = "uvicorn.access"
# HTTP clients log full URLs (query strings, signed-URL tokens) at INFO.
_URL_LOGGING_CLIENTS = ("httpx", "httpx2", "httpcore", "httpcore2")

_ExcInfo = tuple[type[BaseException], BaseException, TracebackType | None]


def redact_sensitive(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in event_dict:
        if key.lower() in SENSITIVE_KEYS:
            event_dict[key] = REDACTED
    return event_dict


def _resolve_exc_info(value: Any) -> _ExcInfo | None:
    if value is True:
        value = sys.exc_info()
    if isinstance(value, BaseException):
        return type(value), value, value.__traceback__
    if isinstance(value, tuple) and len(value) == 3 and isinstance(value[1], BaseException):
        return value
    return None


def _type_name(exc: BaseException) -> str:
    return f"{type(exc).__module__}.{type(exc).__qualname__}"


def sanitize_exception(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Replaces exc_info with the exception type and stack frames only.

    Exception messages are never logged: Pydantic and SQLAlchemy errors embed input
    values, which here means PO contents and customer data.
    """
    exc_info = _resolve_exc_info(event_dict.pop("exc_info", None))
    if exc_info is None:
        return event_dict
    _, exc, tb = exc_info
    event_dict["exc_type"] = _type_name(exc)
    if exc.__cause__ is not None:
        event_dict["exc_cause_type"] = _type_name(exc.__cause__)
    event_dict["exc_frames"] = [
        f"{frame.filename}:{frame.lineno} {frame.name}"
        for frame in traceback.extract_tb(tb)[-MAX_EXC_FRAMES:]
    ]
    return event_dict


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
    """One JSON object per line on stdout (or `stream`), for structlog and stdlib loggers."""
    level_no = logging.getLevelNamesMapping()[level.upper()]
    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_sensitive,
        sanitize_exception,
    ]

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.make_filtering_bound_logger(level_no),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.set_name(HANDLER_NAME)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )
    )

    root = logging.getLogger()
    for existing in [h for h in root.handlers if h.get_name() == HANDLER_NAME]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level_no)

    for name in _UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    logging.getLogger(_UVICORN_ACCESS_LOGGER).disabled = True
    for name in _URL_LOGGING_CLIENTS:
        logging.getLogger(name).setLevel(logging.WARNING)

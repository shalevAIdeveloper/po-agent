import logging
import sys
import traceback
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from types import TracebackType
from typing import Any, TextIO

import structlog

REDACTED = "[REDACTED]"
HANDLER_NAME = "po-agent"
MAX_EXC_FRAMES = 20
MAX_REDACTION_DEPTH = 5

# A key is redacted if its lowercased name CONTAINS any of these fragments
# (client_secret, x-api-key, supabase_jwt, sentry_dsn, ...), or is `key` / ends in
# `_key` / `-key` (service_role_key, encryption_key, hmac_key, ...).
SENSITIVE_KEY_FRAGMENTS: tuple[str, ...] = (
    "authorization",
    "cookie",
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "api-key",
    "jwt",
    "dsn",
    "credential",
    "private_key",
    "signature",
    "database_url",
)

# Exact key names that carry customer data (PII / PO contents).
PII_KEYS = frozenset(
    {
        "email",
        "phone",
        "address",
        "ship_to",
        "bill_to",
        "raw_text",
        "description",
        "price",
        "unit_price",
        "price_per",
        "content",
        "body",
        "claims",
    }
)
# ...and any name ending in one of these (customer_email, ship_to_address, ...).
PII_KEY_SUFFIXES: tuple[str, ...] = ("_email", "_phone", "_address", "_price", "_raw_text")

# Names that match a rule above but carry no secret; they must stay visible
# (LLM usage/cost accounting in M1, request bookkeeping).
NON_SENSITIVE_KEYS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "max_tokens",
        "prompt_tokens",
        "completion_tokens",
        "token_count",
        "sort_key",
        "primary_key",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "idempotency_key",
    }
)

# Third-party loggers interpolate %s args (values, rows, URLs) into the message.
# Only these loggers keep the interpolated message; others log the template.
_INTERPOLATION_ALLOWED_LOGGERS = ("uvicorn.error",)
_INTERPOLATION_ALLOWED_PREFIXES = ("app.",)

# Frames are logged relative to the API root (no home directory in logs).
_API_ROOT = Path(__file__).resolve().parents[1]

# uvicorn's own loggers; access logs are dropped because they include query strings
# and request_context already logs every request without them.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error")
_UVICORN_ACCESS_LOGGER = "uvicorn.access"
# HTTP clients log full URLs (query strings, signed-URL tokens) at INFO.
_URL_LOGGING_CLIENTS = ("httpx", "httpx2", "httpcore", "httpcore2")
# The LLM SDK logs request options (prompt and document content) at DEBUG.
_SDK_LOGGERS = ("anthropic",)

_ExcInfo = tuple[type[BaseException], BaseException, TracebackType | None]


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in NON_SENSITIVE_KEYS:
        return False
    return (
        lowered in PII_KEYS
        or lowered.endswith(PII_KEY_SUFFIXES)
        or lowered == "key"
        or lowered.endswith(("_key", "-key"))
        or any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS)
    )


def _redact(value: Any, depth: int) -> Any:
    if depth >= MAX_REDACTION_DEPTH:
        return REDACTED if isinstance(value, (Mapping, list, tuple)) else value
    if isinstance(value, Mapping):
        return {
            key: REDACTED if is_sensitive_key(str(key)) else _redact(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item, depth + 1) for item in value]
    return value


def redact_sensitive(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in list(event_dict):
        if key.startswith("_"):
            continue  # structlog internals (_record, _from_structlog)
        if is_sensitive_key(key):
            event_dict[key] = REDACTED
        else:
            event_dict[key] = _redact(event_dict[key], 0)
    return event_dict


def drop_foreign_interpolation(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """For stdlib records from third-party loggers, log the template, not the filled message.

    Needs `ProcessorFormatter(pass_foreign_args=True)`: structlog clears `record.args`
    before the pre-chain and hands them over as `positional_args`, which is always
    removed here so the raw args never reach the output.
    """
    args = event_dict.pop("positional_args", None)
    record = event_dict.get("_record")
    if not isinstance(record, logging.LogRecord) or not args:
        return event_dict
    name = record.name
    if name in _INTERPOLATION_ALLOWED_LOGGERS or name.startswith(_INTERPOLATION_ALLOWED_PREFIXES):
        return event_dict
    event_dict["event"] = str(record.msg)
    return event_dict


def _short_path(filename: str) -> str:
    path = Path(filename)
    try:
        return path.resolve().relative_to(_API_ROOT).as_posix()
    except ValueError:
        parts = path.parts
        if "site-packages" in parts:
            return "/".join(parts[parts.index("site-packages") :])
        return path.name


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
    values, which here means PO contents and customer data. That includes a message
    passed explicitly as `exception=str(error)`, and stack dumps (`stack_info`).
    """
    event_dict.pop("exception", None)
    event_dict.pop("stack_info", None)
    exc_info = _resolve_exc_info(event_dict.pop("exc_info", None))
    if exc_info is None:
        return event_dict
    _, exc, tb = exc_info
    event_dict["exc_type"] = _type_name(exc)
    if exc.__cause__ is not None:
        event_dict["exc_cause_type"] = _type_name(exc.__cause__)
    event_dict["exc_frames"] = [
        f"{_short_path(frame.filename)}:{frame.lineno} {frame.name}"
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
        # Before redaction: it needs the raw exc_info tuple.
        sanitize_exception,
        redact_sensitive,
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
            foreign_pre_chain=[drop_foreign_interpolation, *shared_processors],
            pass_foreign_args=True,
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
    for name in (*_URL_LOGGING_CLIENTS, *_SDK_LOGGERS):
        logging.getLogger(name).setLevel(logging.WARNING)

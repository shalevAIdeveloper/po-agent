"""Anthropic adapter: the only module that imports the `anthropic` SDK (ADR 0011).

- Structured output via `output_config.format` (JSON schema); the result is validated
  again by our Pydantic model, which also enforces the constraints the API's schema
  subset can't express (the SDK's `transform_schema` moves them into descriptions).
- No tools, no thinking settings (each model's default), no sampling parameters (the
  current API version rejects `temperature`): depth is set by `output_config.effort`.
- Transport retries are the SDK's (429/408/409/5xx/connection, honours `retry-after`),
  bounded by `max_retries`. The whole call is bounded by our deadline (one full attempt
  + slack); the SDK's own timeout is set beyond it, so a timed-out request (likely
  billed) is never re-sent, and the SDK's unbounded `retry-after` sleep is cut too.
- Everything the SDK would otherwise take from the environment is pinned: the key, the
  base URL, telemetry. Env vars that would change where the key goes, add headers or
  log requests are refused.
"""

import asyncio
import base64
import os
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import cache
from typing import Any, Final

import anthropic
import httpx2
from anthropic.types import (
    ImageBlockParam,
    MessageParam,
    OutputConfigParam,
    TextBlockParam,
    UserMessageParam,
)
from pydantic import BaseModel, SecretStr

from app.llm.client import build_result, log_failure
from app.llm.errors import LLMError, LLMErrorCode
from app.llm.types import ContentPart, ImagePart, LLMResult, StructuredRequest

PROVIDER: Final = "anthropic"
BASE_URL: Final = "https://api.anthropic.com"
# Set in the environment, each would send the key elsewhere, add headers to every request
# or make the SDK log requests outside our redaction.
REFUSED_ENV_VARS: Final = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_CUSTOM_HEADERS",
    "ANTHROPIC_LOG",
)
# The whole call (SDK retries included) gets one full attempt plus this much for quick
# 429/5xx retries: a timed-out attempt, likely billed, is never repeated (`sec` M1).
# Not Final: tests shrink it.
DEADLINE_SLACK_SECONDS = 60.0
# The SDK's own per-attempt timeout is set beyond the deadline, so the SDK never sees a
# timeout and never re-sends a timed-out (billed) request: our deadline always fires first.
SDK_TIMEOUT_MARGIN_SECONDS: Final = 30.0
# Every proxy mount cleared: the SDK builds proxy mounts from HTTPS_PROXY / ALL_PROXY (and
# the Windows registry) whatever `trust_env` says; explicit mounts override them (`sec` L10).
NO_PROXY_MOUNTS: Final[dict[str, None]] = {"http://": None, "https://": None, "all://": None}
# Statuses the SDK retries; seen before a deadline, they make the failure transient.
_RETRYABLE_STATUSES: Final = frozenset({408, 409, 429}) | frozenset(range(500, 600))


@dataclass(slots=True)
class _CallTrace:
    """Requests sent and response statuses received during one extract() (per task)."""

    sent: int = 0
    statuses: list[int] = field(default_factory=list)


_call_trace: ContextVar[_CallTrace | None] = ContextVar("llm_call_trace", default=None)


async def _record_request(_request: httpx2.Request) -> None:
    trace = _call_trace.get()
    if trace is not None:
        trace.sent += 1


async def _record_status(response: httpx2.Response) -> None:
    trace = _call_trace.get()
    if trace is not None:
        trace.statuses.append(response.status_code)


def _deadline_code(trace: _CallTrace) -> LLMErrorCode:
    """What a deadline hit means (`sec` delta 2, 3 + 4).

    A request still waiting for its reply, or a 2xx whose body was still being read, may
    already be generated and billed: that is a timeout and must not be re-run. Only when
    every request got an error answer (the deadline hit while the SDK slept before a
    retry) is it transient.
    """
    if trace.sent > len(trace.statuses) or not trace.statuses:
        return "llm_timeout"
    if any(200 <= status < 300 for status in trace.statuses):
        return "llm_timeout"
    if 429 in trace.statuses:
        return "llm_rate_limited"
    if any(status in _RETRYABLE_STATUSES for status in trace.statuses):
        return "llm_unavailable"
    return "llm_timeout"


@cache
def provider_schema(output_model: type[BaseModel]) -> dict[str, Any]:
    """The JSON Schema sent to the API: our model's schema in the supported subset."""
    return anthropic.transform_schema(output_model.model_json_schema(mode="validation"))


def _content_blocks(parts: tuple[ContentPart, ...]) -> list[TextBlockParam | ImageBlockParam]:
    blocks: list[TextBlockParam | ImageBlockParam] = []
    for part in parts:
        if isinstance(part, ImagePart):
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": part.media_type,
                        "data": base64.standard_b64encode(part.data).decode("ascii"),
                    },
                }
            )
        else:
            blocks.append({"type": "text", "text": part.text})
    return blocks


def _error_code(error: anthropic.APIError) -> LLMErrorCode:
    if isinstance(error, anthropic.APITimeoutError):
        return "llm_timeout"
    if isinstance(error, anthropic.APIConnectionError):
        return "llm_unavailable"
    if isinstance(error, anthropic.RateLimitError):
        return "llm_rate_limited"
    if isinstance(error, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return "llm_auth"
    if isinstance(error, anthropic.APIStatusError):
        if error.status_code >= 500:
            return "llm_unavailable"
        if 400 <= error.status_code < 500:
            return "llm_bad_request"
    return "llm_provider_error"


class AnthropicLLMClient:
    provider = PROVIDER

    def __init__(
        self,
        *,
        api_key: SecretStr,
        timeout_seconds: float,
        max_retries: int,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        refused = [name for name in REFUSED_ENV_VARS if os.environ.get(name, "").strip()]
        if refused:
            # Names only, never values.
            raise LLMError("llm_config", detail=f"refused env var(s) set: {', '.join(refused)}")
        key = api_key.get_secret_value().strip()
        if not key:
            raise LLMError("llm_config", detail="LLM_API_KEY is empty")
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        if http_client is None:
            # trust_env=False: no CA-bundle env vars (SSL_CERT_FILE, ...); explicit empty
            # mounts: no proxy from env or registry (`sec` L10). SDK defaults kept.
            http_client = anthropic.DefaultAsyncHttpxClient(
                trust_env=False,
                mounts=NO_PROXY_MOUNTS,
            )
        hooks = http_client.event_hooks
        http_client.event_hooks = {
            "request": [*hooks.get("request", []), _record_request],
            "response": [*hooks.get("response", []), _record_status],
        }
        # An explicit api_key stops the SDK from reading any credential env var or profile.
        self._client = anthropic.AsyncAnthropic(
            api_key=key,
            base_url=BASE_URL,
            timeout=self.deadline_seconds + SDK_TIMEOUT_MARGIN_SECONDS,
            max_retries=max_retries,
            open_telemetry=False,
            http_client=http_client,
        )

    @property
    def deadline_seconds(self) -> float:
        return self._timeout_seconds + DEADLINE_SLACK_SECONDS

    async def extract[T: BaseModel](self, request: StructuredRequest[T]) -> LLMResult[T]:
        user_turn: UserMessageParam = {"role": "user", "content": _content_blocks(request.content)}
        messages: list[MessageParam] = [user_turn]
        output_config: OutputConfigParam = {
            "effort": request.effort,
            "format": {"type": "json_schema", "schema": provider_schema(request.output_model)},
        }
        started = time.perf_counter()
        trace = _CallTrace()
        token = _call_trace.set(trace)
        try:
            async with asyncio.timeout(self.deadline_seconds):
                message = await self._client.messages.create(
                    model=request.model,
                    max_tokens=request.max_output_tokens,
                    system=request.system,
                    messages=messages,
                    output_config=output_config,
                )
        except TimeoutError:
            code = _deadline_code(trace)
            self._fail(request, code, started)
            raise LLMError(code) from None
        except anthropic.APIError as error:
            code = _error_code(error)
            self._fail(request, code, started)
            status = error.status_code if isinstance(error, anthropic.APIStatusError) else None
            request_id = error.request_id if isinstance(error, anthropic.APIStatusError) else None
            # `from None`: the SDK error's message and body may quote the request.
            raise LLMError(code, status_code=status, provider_request_id=request_id) from None
        except anthropic.AnthropicError:
            # Any other SDK failure (`sec` L2): same stable code, no SDK text.
            self._fail(request, "llm_provider_error", started)
            raise LLMError("llm_provider_error") from None
        finally:
            _call_trace.reset(token)

        texts = [block.text for block in message.content if block.type == "text"]
        return build_result(
            request,
            provider=PROVIDER,
            model=message.model,
            raw_output="".join(texts) if texts else None,
            stop_reason=message.stop_reason,
            provider_request_id=message._request_id,
            latency_ms=_elapsed_ms(started),
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        )

    def _fail(self, request: StructuredRequest[Any], code: str, started: float) -> None:
        log_failure(request, provider=PROVIDER, code=code, latency_ms=_elapsed_ms(started))


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)

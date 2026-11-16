import json
from collections.abc import Callable
from typing import Any, Literal

import httpx2
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.llm.providers.anthropic import AnthropicLLMClient
from app.llm.types import ContentPart, StructuredRequest, TextPart

API_KEY = "sk-ant-test-0123456789abcdef"
SYSTEM = "SYSTEM-PROMPT-MARKER: you extract purchase orders"
PO_TEXT = "ACME 123 Main St $4.50"  # stands in for document content
MODEL = "claude-sonnet-5-5"


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["po", "other"]
    score: float = Field(ge=0, le=1)


VALID_OUTPUT = '{"kind": "po", "score": 0.9}'


def make_request(
    content: tuple[ContentPart, ...] = (TextPart(PO_TEXT),), **overrides: Any
) -> StructuredRequest[Answer]:
    values: dict[str, Any] = {
        "system": SYSTEM,
        "content": content,
        "output_model": Answer,
        "prompt_version": "extract_po/v1",
        "schema_version": "answer.v1",
        "model": MODEL,
        "max_output_tokens": 1000,
        "effort": "medium",
    }
    values.update(overrides)
    return StructuredRequest(**values)


def message_body(
    text: str | None = VALID_OUTPUT,
    *,
    stop_reason: str = "end_turn",
    model: str = MODEL,
    input_tokens: int = 1000,
    output_tokens: int = 200,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "thinking", "thinking": "", "signature": "sig"}]
    if text is not None:
        content.append({"type": "text", "text": text})
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def ok_response(body: dict[str, Any] | None = None, request_id: str = "req_ok") -> httpx2.Response:
    return httpx2.Response(
        200, json=body if body is not None else message_body(), headers={"request-id": request_id}
    )


Handler = Callable[[httpx2.Request], Any]


def adapter(
    handler: Handler,
    *,
    timeout_seconds: float = 5.0,
    max_retries: int = 0,
    api_key: str = API_KEY,
) -> AnthropicLLMClient:
    return AnthropicLLMClient(
        api_key=SecretStr(api_key),
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


def body_of(request: httpx2.Request) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(request.content)
    return payload

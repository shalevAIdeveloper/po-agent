"""Test doubles (unit tests, CI, e2e). None of them can make a network call.

- `FakeLLMClient`: scripted responses, records the requests it got.
- `ReplayLLMClient`: answers from cassettes keyed by the request fingerprint; a miss is
  an error (`llm_replay_miss`), never a live call.
- `RecordingLLMClient`: wraps a real client and writes a cassette per call (evals
  `--live --record` only). A cassette holds the model's output, usage, model, latency
  and the provider request id: never request headers, the key or the prompt.
"""

import json
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel

from app.llm.client import LLMClient, build_result
from app.llm.errors import LLMError
from app.llm.types import LLMResult, StructuredRequest

CASSETTE_VERSION: Final = 1
_CASSETTE_KEYS: Final = frozenset(
    {
        "cassette_version",
        "fingerprint",
        "prompt_version",
        "schema_version",
        "provider",
        "model",
        "raw_output",
        "stop_reason",
        "provider_request_id",
        "latency_ms",
        "input_tokens",
        "output_tokens",
    }
)


@dataclass(frozen=True, slots=True)
class FakeResponse:
    raw_output: str | None
    stop_reason: str | None = "end_turn"
    input_tokens: int = 0
    output_tokens: int = 0
    model: str | None = None  # defaults to the request's model


class FakeLLMClient:
    provider = "fake"

    def __init__(self, responses: Iterable[FakeResponse | LLMError] = ()) -> None:
        self._responses: deque[FakeResponse | LLMError] = deque(responses)
        self.requests: list[StructuredRequest[Any]] = []

    def add(self, response: FakeResponse | LLMError) -> None:
        self._responses.append(response)

    async def extract[T: BaseModel](self, request: StructuredRequest[T]) -> LLMResult[T]:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("FakeLLMClient: no scripted response left")
        response = self._responses.popleft()
        if isinstance(response, LLMError):
            raise response
        return build_result(
            request,
            provider=self.provider,
            model=response.model or request.model,
            raw_output=response.raw_output,
            stop_reason=response.stop_reason,
            provider_request_id=None,
            latency_ms=0,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )


def cassette_path(directory: Path, request: StructuredRequest[Any]) -> Path:
    return directory / f"{request.fingerprint()}.json"


def to_cassette(request: StructuredRequest[Any], result: LLMResult[Any]) -> dict[str, Any]:
    return {
        "cassette_version": CASSETTE_VERSION,
        "fingerprint": request.fingerprint(),
        "prompt_version": request.prompt_version,
        "schema_version": request.schema_version,
        "provider": result.provider,
        "model": result.model,
        "raw_output": result.raw_output,
        "stop_reason": result.stop_reason,
        "provider_request_id": result.provider_request_id,
        "latency_ms": result.latency_ms,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
    }


class ReplayLLMClient:
    provider = "replay"

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    async def extract[T: BaseModel](self, request: StructuredRequest[T]) -> LLMResult[T]:
        path = cassette_path(self._directory, request)
        if not path.is_file():
            raise LLMError("llm_replay_miss")
        cassette = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(cassette, dict)
            or set(cassette) != _CASSETTE_KEYS
            or cassette["cassette_version"] != CASSETTE_VERSION
            or cassette["fingerprint"] != request.fingerprint()
        ):
            # A hand-edited or foreign file is a miss, not a result.
            raise LLMError("llm_replay_miss", detail="cassette does not match the request")
        # Parsing, cost and the log line are recomputed exactly as for a live call.
        return build_result(
            request,
            provider=str(cassette["provider"]),
            model=str(cassette["model"]),
            raw_output=cassette["raw_output"],
            stop_reason=cassette["stop_reason"],
            provider_request_id=cassette["provider_request_id"],
            latency_ms=int(cassette["latency_ms"]),
            input_tokens=int(cassette["input_tokens"]),
            output_tokens=int(cassette["output_tokens"]),
        )


class RecordingLLMClient:
    def __init__(self, inner: LLMClient, directory: Path) -> None:
        self._inner = inner
        self._directory = directory
        self.provider = inner.provider

    async def extract[T: BaseModel](self, request: StructuredRequest[T]) -> LLMResult[T]:
        result = await self._inner.extract(request)
        self._directory.mkdir(parents=True, exist_ok=True)
        cassette = json.dumps(to_cassette(request, result), indent=2, ensure_ascii=False) + "\n"
        cassette_path(self._directory, request).write_text(cassette, encoding="utf-8", newline="\n")
        return result

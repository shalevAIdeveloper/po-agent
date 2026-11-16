"""Provider-neutral request/result types for structured LLM calls (ADR 0011)."""

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final, Literal

from pydantic import BaseModel

Effort = Literal["low", "medium", "high", "xhigh", "max"]
ImageMediaType = Literal["image/png", "image/jpeg", "image/gif", "image/webp"]
# ok: parsed is set. The others are billed calls without a usable result: they are
# results (with usage and cost), not exceptions, so every paid call can be recorded.
Outcome = Literal["ok", "invalid_output", "refusal", "truncated"]

FINGERPRINT_VERSION: Final = 1


@dataclass(frozen=True, slots=True)
class TextPart:
    text: str


@dataclass(frozen=True, slots=True)
class ImagePart:
    data: bytes = field(repr=False)
    media_type: ImageMediaType


ContentPart = TextPart | ImagePart


@dataclass(frozen=True, slots=True)
class StructuredRequest[T: BaseModel]:
    """One schema-constrained call. Document content goes only into `content` (user turn)."""

    system: str = field(repr=False)
    content: tuple[ContentPart, ...] = field(repr=False)
    output_model: type[T]
    prompt_version: str  # "<name>/v<N>"
    schema_version: str  # e.g. "po_extraction.v1"
    model: str
    max_output_tokens: int
    effort: Effort = "medium"

    def fingerprint(self) -> str:
        """Stable key of everything that decides the output (replay cassettes, PR 7)."""
        parts: list[dict[str, str]] = []
        for part in self.content:
            if isinstance(part, TextPart):
                parts.append({"type": "text", "text": part.text})
            else:
                digest = hashlib.sha256(part.data).hexdigest()
                parts.append({"type": "image", "media_type": part.media_type, "sha256": digest})
        # The schema itself, not only its version label: an edit without a version bump
        # must not replay old answers (`sec` L5).
        schema = json.dumps(
            self.output_model.model_json_schema(mode="validation"),
            sort_keys=True,
            separators=(",", ":"),
        )
        payload = {
            "v": FINGERPRINT_VERSION,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "schema_sha256": hashlib.sha256(schema.encode("utf-8")).hexdigest(),
            "model": self.model,
            "effort": self.effort,
            "max_output_tokens": self.max_output_tokens,
            "system": self.system,
            "content": parts,
        }
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A sanitized validation error: field path + message, never the offending input."""

    path: str
    type: str
    message: str


@dataclass(frozen=True, slots=True)
class LLMResult[T: BaseModel]:
    outcome: Outcome
    # Extracted document data: never in a repr or a log line (`sec` L7).
    parsed: T | None = field(repr=False)
    validation_errors: tuple[ValidationIssue, ...]
    # The model's JSON text as returned (document data: stored, never logged).
    raw_output: str | None = field(repr=False)
    provider: str
    model: str
    provider_request_id: str | None
    stop_reason: str | None
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None

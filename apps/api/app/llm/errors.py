"""LLM errors carry a stable code only, never provider message text (it can echo input)."""

from typing import Final, Literal

LLMErrorCode = Literal[
    "llm_config",  # missing key/model, unsafe environment: a deploy problem, not transient
    "llm_timeout",  # an attempt or the whole call ran out of time
    "llm_rate_limited",  # 429 after the transport retries
    "llm_unavailable",  # 5xx / overloaded / connection error after the transport retries
    "llm_auth",  # 401/403: wrong or revoked key, or no access to the model
    "llm_bad_request",  # 4xx the request itself caused (too large, invalid schema, ...)
    "llm_provider_error",  # any other provider failure
    "llm_replay_miss",  # replay has no cassette for this request (never a live call)
]

# Codes worth retrying later at the job level (PR 12 backs off via run_after).
# Not `llm_timeout`: the timeout covers a full-length reply, so a timed-out call was
# likely generated and billed; retrying it would pay again for the same document (`sec` M1).
TRANSIENT_CODES: Final[frozenset[str]] = frozenset({"llm_rate_limited", "llm_unavailable"})


class LLMError(Exception):
    def __init__(
        self,
        code: LLMErrorCode,
        *,
        status_code: int | None = None,
        provider_request_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        # `detail` is our own fixed text (e.g. setting names), never provider or document text.
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code: LLMErrorCode = code
        self.detail = detail
        self.status_code = status_code
        self.provider_request_id = provider_request_id

    @property
    def transient(self) -> bool:
        return self.code in TRANSIENT_CODES

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.llm.errors import LLMError
from app.llm.fake import (
    FakeLLMClient,
    FakeResponse,
    RecordingLLMClient,
    ReplayLLMClient,
    cassette_path,
)
from tests.llm.helpers import (
    API_KEY,
    PO_TEXT,
    SYSTEM,
    VALID_OUTPUT,
    Answer,
    adapter,
    make_request,
    ok_response,
)


async def test_fake_returns_scripted_responses_and_records_requests() -> None:
    fake = FakeLLMClient([FakeResponse(VALID_OUTPUT, input_tokens=100, output_tokens=10)])
    request = make_request()

    result = await fake.extract(request)

    assert result.parsed == Answer(kind="po", score=0.9)
    assert result.provider == "fake"
    assert fake.requests == [request]


async def test_fake_raises_scripted_errors_and_runs_dry() -> None:
    fake = FakeLLMClient([LLMError("llm_timeout")])

    with pytest.raises(LLMError, match="llm_timeout"):
        await fake.extract(make_request())
    with pytest.raises(AssertionError, match="no scripted response"):
        await fake.extract(make_request())


async def test_replay_miss_is_an_error_never_a_live_call(tmp_path: Path) -> None:
    with pytest.raises(LLMError) as raised:
        await ReplayLLMClient(tmp_path).extract(make_request())
    assert raised.value.code == "llm_replay_miss"


async def test_record_then_replay_gives_the_same_result(tmp_path: Path) -> None:
    request = make_request()
    live = adapter(lambda _r: ok_response(request_id="req_rec"))

    recorded = await RecordingLLMClient(live, tmp_path).extract(request)
    replayed = await ReplayLLMClient(tmp_path).extract(request)

    assert replayed.parsed == recorded.parsed
    assert replayed.cost_usd == recorded.cost_usd
    assert replayed.provider_request_id == "req_rec"
    assert (replayed.input_tokens, replayed.output_tokens) == (1000, 200)


async def test_cassette_has_no_key_headers_or_prompt(tmp_path: Path) -> None:
    request = make_request()
    live = adapter(lambda _r: ok_response())

    await RecordingLLMClient(live, tmp_path).extract(request)

    text = cassette_path(tmp_path, request).read_text(encoding="utf-8")
    for leaked in (API_KEY, "Bearer", "authorization", "anthropic-version", SYSTEM, PO_TEXT):
        assert leaked not in text
    assert json.loads(text)["raw_output"] == VALID_OUTPUT


async def test_replay_of_a_different_request_misses(tmp_path: Path) -> None:
    live = adapter(lambda _r: ok_response())
    await RecordingLLMClient(live, tmp_path).extract(make_request())

    with pytest.raises(LLMError) as raised:
        await ReplayLLMClient(tmp_path).extract(make_request(prompt_version="extract_po/v2"))
    assert raised.value.code == "llm_replay_miss"


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda c: "{not json " + PO_TEXT,
        lambda c: json.dumps(c | {"latency_ms": "12"}),
        lambda c: json.dumps(c | {"input_tokens": -1}),
        lambda c: json.dumps(c | {"input_tokens": True}),
        lambda c: json.dumps(c | {"raw_output": {"kind": "po"}}),
        lambda c: json.dumps([c]),
    ],
)
async def test_malformed_cassette_is_a_miss_without_its_content(
    tmp_path: Path, corrupt: Callable[[dict[str, Any]], str]
) -> None:
    # `sec` L4: never a crash that carries the file's content.
    request = make_request()
    await RecordingLLMClient(adapter(lambda _r: ok_response()), tmp_path).extract(request)
    path = cassette_path(tmp_path, request)
    path.write_text(corrupt(json.loads(path.read_text(encoding="utf-8"))), encoding="utf-8")

    with pytest.raises(LLMError) as raised:
        await ReplayLLMClient(tmp_path).extract(request)
    assert raised.value.code == "llm_replay_miss"
    assert PO_TEXT not in str(raised.value)
    assert raised.value.__cause__ is None


async def test_tampered_cassette_misses(tmp_path: Path) -> None:
    request = make_request()
    await RecordingLLMClient(adapter(lambda _r: ok_response()), tmp_path).extract(request)
    path = cassette_path(tmp_path, request)
    cassette = json.loads(path.read_text(encoding="utf-8"))
    cassette["extra"] = "smuggled"
    path.write_text(json.dumps(cassette), encoding="utf-8")

    with pytest.raises(LLMError) as raised:
        await ReplayLLMClient(tmp_path).extract(request)
    assert raised.value.code == "llm_replay_miss"

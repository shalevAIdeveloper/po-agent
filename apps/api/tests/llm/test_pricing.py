from decimal import Decimal

import pytest

from app.llm.pricing import PRICES, cost_usd


def test_sonnet_cost_from_usage() -> None:
    # 12,345 * $2/M + 1,000 * $10/M
    assert cost_usd("claude-sonnet-5-5", 12_345, 1_000) == Decimal("0.034690")


def test_sonnet_has_one_price_for_the_full_context() -> None:
    assert cost_usd("claude-sonnet-5-5", 900_000, 0) == Decimal("1.800000")


@pytest.mark.parametrize(
    ("input_tokens", "expected"),
    [
        # at the threshold: standard ($0.10 / $0.50)
        (100_000, Decimal("0.010500")),
        # over it: the whole request at $0.50 / $2.50
        (100_001, Decimal("0.052501")),
    ],
)
def test_haiku_prices_the_whole_request_by_prompt_length(
    input_tokens: int, expected: Decimal
) -> None:
    assert cost_usd("claude-haiku-5-5", input_tokens, 1_000) == expected


def test_unknown_model_is_none_never_a_guess() -> None:
    assert cost_usd("claude-sonnet-9", 1_000, 1_000) is None


def test_every_tiered_price_has_both_parts() -> None:
    for price in PRICES.values():
        assert (price.long_prompt_over is None) == (price.long_prompt is None)

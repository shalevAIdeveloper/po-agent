"""USD per 1M tokens, Anthropic first-party API, standard (non-batch, global routing).

Checked against https://platform.claude.com/docs/en/about-claude/pricing on PRICES_CHECKED.
An unknown model gets `None` (and a warning from the caller), never a guess: tokens are
stored too, so cost can be recomputed when the table is updated.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

PRICES_CHECKED: Final = date(2026, 11, 15)
_MTOK: Final = Decimal(1_000_000)


@dataclass(frozen=True, slots=True)
class Rate:
    input_per_mtok: Decimal
    output_per_mtok: Decimal


@dataclass(frozen=True, slots=True)
class Price:
    standard: Rate
    # Some models price the whole request higher when its prompt is longer than this
    # many input tokens (Haiku 5.5: over 100,000). None = one price for the full context.
    long_prompt_over: int | None = None
    long_prompt: Rate | None = None

    def rate_for(self, input_tokens: int) -> Rate:
        over = self.long_prompt_over
        if over is not None and self.long_prompt is not None and input_tokens > over:
            return self.long_prompt
        return self.standard


PRICES: Final[dict[str, Price]] = {
    "claude-sonnet-5-5": Price(standard=Rate(Decimal("2"), Decimal("10"))),
    "claude-haiku-5-5": Price(
        standard=Rate(Decimal("0.10"), Decimal("0.50")),
        long_prompt_over=100_000,
        long_prompt=Rate(Decimal("0.50"), Decimal("2.50")),
    ),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal | None:
    """Cost of one call; output tokens include thinking tokens (billed as output)."""
    price = PRICES.get(model)
    if price is None:
        return None
    rate = price.rate_for(input_tokens)
    total = input_tokens * rate.input_per_mtok + output_tokens * rate.output_per_mtok
    return (total / _MTOK).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

"""Estimate what Gemini calls cost from their token counts."""

from steel_slag_slm.agents.runner import TokenUsage
from steel_slag_slm.config import ModelPrice

TOKENS_PER_PRICE_UNIT = 1_000_000


def estimate_cost_usd(usage: TokenUsage, price: ModelPrice | None) -> float | None:
    """List-price cost of the tokens, or None when the model's price isn't configured."""
    if price is None:
        return None
    return (
        usage.input_tokens * price.input + usage.output_tokens * price.output
    ) / TOKENS_PER_PRICE_UNIT

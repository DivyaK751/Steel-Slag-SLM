"""Turn token counts into an estimated price."""

from steel_slag_slm.config import ModelPrices
from steel_slag_slm.schemas import TokenUsage

TOKENS_PER_MILLION = 1_000_000


def estimate_cost_usd(usage: TokenUsage, prices: ModelPrices) -> float:
    """Estimate what the tokens cost; thinking tokens are billed at the output rate."""
    billed_output = usage.output_tokens + usage.thinking_tokens
    return (
        usage.input_tokens * prices.input_usd_per_million
        + billed_output * prices.output_usd_per_million
    ) / TOKENS_PER_MILLION

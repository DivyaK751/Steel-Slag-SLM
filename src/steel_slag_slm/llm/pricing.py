"""Turn Gemini token counts and BigQuery bytes into US$ costs at list prices."""

from steel_slag_slm.config import TokenPrices
from steel_slag_slm.schemas import TokenUsage

BYTES_PER_TIB = 2**40


def gemini_cost_usd(usage: TokenUsage, prices: TokenPrices) -> float:
    """Cost of one or more Gemini calls; cached input tokens are billed at the cached rate."""
    uncached_tokens = usage.input_tokens - usage.cached_tokens
    return (
        uncached_tokens * prices.input
        + usage.cached_tokens * prices.cached_input
        + usage.output_tokens * prices.output
    ) / 1_000_000


def bigquery_query_cost_usd(bytes_billed: int, usd_per_tib: float) -> float:
    """Cost of on-demand BigQuery queries that billed this many bytes."""
    return bytes_billed / BYTES_PER_TIB * usd_per_tib

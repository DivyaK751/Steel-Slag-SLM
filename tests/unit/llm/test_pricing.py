import pytest

from steel_slag_slm.config import TokenPrices
from steel_slag_slm.llm.pricing import bigquery_query_cost_usd, gemini_cost_usd
from steel_slag_slm.schemas import TokenUsage

PRICES = TokenPrices(input=2.0, cached_input=0.2, output=12.0)


def test_gemini_cost_bills_cached_input_at_the_cached_rate() -> None:
    usage = TokenUsage(input_tokens=1_000_000, cached_tokens=500_000, output_tokens=100_000)
    # 500k uncached x $2/M + 500k cached x $0.2/M + 100k output x $12/M
    assert gemini_cost_usd(usage, PRICES) == pytest.approx(1.0 + 0.1 + 1.2)


def test_bigquery_cost_is_bytes_billed_at_the_per_tib_price() -> None:
    ten_mib = 10 * 2**20  # BigQuery's minimum bill per query
    assert bigquery_query_cost_usd(ten_mib, 6.25) == pytest.approx(6.25 * ten_mib / 2**40)

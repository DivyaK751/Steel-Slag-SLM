import pytest

from steel_slag_slm.config import ModelPrices
from steel_slag_slm.llm.costs import estimate_cost_usd
from steel_slag_slm.schemas import TokenUsage


def test_estimate_cost_usd_bills_thinking_at_the_output_rate() -> None:
    prices = ModelPrices(input_usd_per_million=1.0, output_usd_per_million=4.0)
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=250_000, thinking_tokens=250_000)
    assert estimate_cost_usd(usage, prices) == pytest.approx(1.0 + 2.0)


def test_token_usage_adds_up_field_by_field() -> None:
    total = TokenUsage(10, 2, 1) + TokenUsage(5, 3, 0)
    assert total == TokenUsage(15, 5, 1)

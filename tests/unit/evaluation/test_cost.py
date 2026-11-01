import pytest

from steel_slag_slm.config import ModelPrice
from steel_slag_slm.evaluation.cost import estimate_cost_usd
from steel_slag_slm.schemas import TokenUsage


def test_estimate_cost_uses_per_million_prices() -> None:
    usage = TokenUsage(input_tokens=2_000_000, cached_tokens=0, output_tokens=500_000)
    assert estimate_cost_usd(usage, ModelPrice(input=0.3, output=2.5)) == pytest.approx(1.85)


def test_estimate_cost_is_none_without_a_price() -> None:
    assert estimate_cost_usd(TokenUsage(10, 0, 10), None) is None

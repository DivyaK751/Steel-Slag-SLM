from typing import Any

import pytest
import transformers

from steel_slag_slm.rag.token_budget import TokenBudget, load_token_budget


def test_fits_compares_the_count_with_a_share_of_the_budget() -> None:
    budget = TokenBudget(count=lambda text: len(text.split()), max_tokens=10)

    assert budget.fits("one two three four five")
    assert not budget.fits("one two three four five six", share=0.5)


def test_load_token_budget_counts_with_the_models_tokenizer_without_special_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []

    class FakeTokenizer:
        def encode(self, text: str, add_special_tokens: bool) -> list[int]:
            calls.append(add_special_tokens)
            return list(range(len(text)))

    monkeypatch.setattr(transformers.AutoTokenizer, "from_pretrained", lambda name: FakeTokenizer())

    budget = load_token_budget("reranker-a", max_tokens=384)

    assert budget.count("abcd") == 4 and calls == [False]


def test_load_token_budget_rejects_a_nonpositive_limit() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        load_token_budget("reranker-a", max_tokens=0)

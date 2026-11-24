"""Count a model's tokens, so a chunk is sized by what the model actually reads."""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TokenBudget:
    """The most tokens of one model a chunk's text may hold, and how to count them."""

    count: Callable[[str], int]
    max_tokens: int

    def fits(self, text: str, share: float = 1.0) -> bool:
        """Return whether text takes at most this share of the budget."""
        return self.count(text) <= self.max_tokens * share


def load_token_budget(model_name: str, max_tokens: int) -> TokenBudget:
    """Count with the model's own tokenizer, without special tokens (the budget leaves room).

    Raises:
        ValueError: If max_tokens is not positive.
    """
    if max_tokens <= 0:
        raise ValueError("max_tokens must be greater than zero")
    # Imported here: transformers is slow to load and only indexing needs a tokenizer
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_name)
    return TokenBudget(
        count=lambda text: len(tokenizer.encode(text, add_special_tokens=False)),
        max_tokens=max_tokens,
    )

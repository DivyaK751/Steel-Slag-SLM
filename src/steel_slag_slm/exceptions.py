"""Custom exceptions for the package."""

from steel_slag_slm.schemas import TokenUsage


class BudgetExhaustedError(Exception):
    """The OpenAlex daily budget for this API key is used up."""


class OpenAlexRequestError(Exception):
    """A request to OpenAlex failed for a temporary reason (timeout, dropped connection, 5xx)."""


class AgentOutputError(Exception):
    """An agent returned no answer, or one that doesn't match its output schema.

    answer keeps the raw text it did return, cut_off says the model stopped at its output
    limit, and usage holds the tokens the failed call still used (and was billed for).
    """

    def __init__(
        self,
        message: str,
        answer: str = "",
        cut_off: bool = False,
        usage: TokenUsage | None = None,
    ) -> None:
        super().__init__(message)
        self.answer = answer
        self.cut_off = cut_off
        self.usage = usage


class PapersNotFoundError(ValueError):
    """Some requested paper IDs have no matching PDF or figures folder."""

    def __init__(self, missing_ids: list[str]) -> None:
        super().__init__(f"No PDF or figures folder for: {', '.join(missing_ids)}")
        self.missing_ids = missing_ids

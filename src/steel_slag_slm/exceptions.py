"""Custom exceptions for the package."""


class BudgetExhaustedError(Exception):
    """The OpenAlex daily budget for this API key is used up."""


class OpenAlexRequestError(Exception):
    """A request to OpenAlex failed for a temporary reason (timeout, dropped connection, 5xx)."""


class AgentOutputError(Exception):
    """An agent returned no answer, or one that doesn't match its output schema."""


class PapersNotFoundError(ValueError):
    """Some requested paper IDs have no matching PDF or figures folder."""

    def __init__(self, missing_ids: list[str]) -> None:
        super().__init__(f"No PDF or figures folder for: {', '.join(missing_ids)}")
        self.missing_ids = missing_ids

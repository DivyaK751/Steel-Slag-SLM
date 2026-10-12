"""Custom exceptions for the package."""


class BudgetExhaustedError(Exception):
    """The OpenAlex daily budget for this API key is used up."""

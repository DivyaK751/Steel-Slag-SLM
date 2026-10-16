"""Record types shared across modules."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Paper:
    """One OpenAlex work, reduced to the fields the pipeline uses."""

    openalex_id: str  # short form, e.g. "W2068066265"
    title: str
    abstract: str
    doi: str | None
    year: int | None
    free_pdf_url: str | None  # publisher or repository copy, costs nothing
    paid_pdf_url: str | None  # OpenAlex cached copy, billed per download
    authors: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    language: str | None = None  # ISO 639-1 code from OpenAlex, e.g. "en"

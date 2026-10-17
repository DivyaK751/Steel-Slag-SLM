"""Record types shared across modules."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, model_validator

HIGH_CONFIDENCE_MIN_SCORE = 0.85
MEDIUM_CONFIDENCE_MIN_SCORE = 0.60


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


@dataclass(frozen=True, slots=True)
class PaperForScreening:
    """One row of the BigQuery papers table, reduced to what the screening agent reads."""

    openalex_id: str
    title: str
    abstract: str
    year: int | None
    keywords: tuple[str, ...]


def confidence_for_score(score: float) -> Literal["High", "Medium", "Low"]:
    """Map a 0-1 score to the confidence band the screening prompt defines."""
    if score >= HIGH_CONFIDENCE_MIN_SCORE:
        return "High"
    if score >= MEDIUM_CONFIDENCE_MIN_SCORE:
        return "Medium"
    return "Low"


class RelevanceDecision(BaseModel):
    """The screening agent's verdict on one paper; also the agent's output schema."""

    openalex_id: str = ""
    relevant: Literal["Yes", "No", "Unclear"]
    relevance_score: float = Field(ge=0.0, le=1.0)
    relevance_confidence: Literal["High", "Medium", "Low"]
    relevance_reason: str
    supporting_excerpt: str | None = None

    @model_validator(mode="after")
    def _confidence_follows_score(self) -> "RelevanceDecision":
        # The model sometimes picks a band that contradicts its own score; the score wins
        self.relevance_confidence = confidence_for_score(self.relevance_score)
        return self

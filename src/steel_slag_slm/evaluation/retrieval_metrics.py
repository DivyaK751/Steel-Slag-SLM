"""Score retrieval results against labelled questions, including questions with no answer."""

from collections.abc import Sequence
from dataclasses import dataclass

from steel_slag_slm.config import RetrievalQuery
from steel_slag_slm.schemas import Chunk


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """How retrieval handled one question.

    Answerable questions carry the 1-based rank of the first relevant chunk (None if it was
    not in the results). Unanswerable questions carry the best score and whether the search
    abstained, meaning that score fell below the threshold.
    """

    query: str
    answerable: bool
    rank: int | None = None
    top_score: float | None = None
    abstained: bool | None = None


def _normalize(text: str) -> str:
    return " ".join(text.split()).lower()


def is_relevant(chunk: Chunk, question: RetrievalQuery) -> bool:
    """A chunk answers the question when it is from the right paper and kind and has the text."""
    return (
        chunk.paper_id == question.paper_id
        and chunk.kind == question.source
        and _normalize(question.expected_text or "") in _normalize(chunk.text)
    )


def first_relevant_rank(ranked_chunks: Sequence[Chunk], question: RetrievalQuery) -> int | None:
    """Return the 1-based rank of the first relevant chunk, or None if there is none."""
    for rank, chunk in enumerate(ranked_chunks, start=1):
        if is_relevant(chunk, question):
            return rank
    return None


def hit_rate(results: Sequence[RetrievalResult], top_k: int) -> float:
    """Share of answerable questions whose first relevant chunk ranked within top_k."""
    answerable = [result for result in results if result.answerable]
    if not answerable:
        return 0.0
    hits = sum(1 for result in answerable if result.rank is not None and result.rank <= top_k)
    return hits / len(answerable)


def mean_reciprocal_rank(results: Sequence[RetrievalResult]) -> float:
    """Average of 1/rank over the answerable questions; a miss scores 0."""
    answerable = [result for result in results if result.answerable]
    if not answerable:
        return 0.0
    return sum(1 / result.rank for result in answerable if result.rank) / len(answerable)


def abstain_rate(results: Sequence[RetrievalResult]) -> float:
    """Share of unanswerable questions where the search correctly abstained."""
    unanswerable = [result for result in results if not result.answerable]
    if not unanswerable:
        return 0.0
    return sum(1 for result in unanswerable if result.abstained) / len(unanswerable)

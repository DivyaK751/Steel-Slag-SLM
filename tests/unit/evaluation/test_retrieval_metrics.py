import pytest

from steel_slag_slm.config import RetrievalQuery
from steel_slag_slm.evaluation.retrieval_metrics import (
    RetrievalResult,
    abstain_rate,
    first_relevant_rank,
    hit_rate,
    is_relevant,
    mean_reciprocal_rank,
)
from steel_slag_slm.schemas import Chunk


def _chunk(paper_id: str, text: str, kind: str = "text") -> Chunk:
    return Chunk(paper_id=paper_id, kind=kind, section="", text=text, position=0)


def _question(text: str = "free lime", source: str = "text") -> RetrievalQuery:
    return RetrievalQuery(
        query="q", answerable=True, paper_id="W1", source=source, expected_text=text
    )


def test_is_relevant_needs_the_paper_kind_and_text_ignoring_case_and_spacing():
    chunk = _chunk("W1", "Free   LIME\ndrives volume change.")
    assert is_relevant(chunk, _question("free lime drives"))
    assert not is_relevant(_chunk("W2", "Free lime"), _question("free lime"))
    assert not is_relevant(chunk, _question("sinter"))


def test_is_relevant_requires_the_source_kind():
    table_chunk = _chunk("W1", "BOFS | 45-60", kind="table")
    assert is_relevant(table_chunk, _question("bofs | 45-60", source="table"))
    assert not is_relevant(table_chunk, _question("bofs | 45-60", source="text"))


def test_first_relevant_rank_is_one_based_and_none_when_absent():
    ranked = [_chunk("W2", "x"), _chunk("W1", "free lime"), _chunk("W1", "free lime again")]
    assert first_relevant_rank(ranked, _question("free lime")) == 2
    assert first_relevant_rank(ranked, _question("sinter")) is None


def test_answerable_question_needs_its_answer_fields():
    with pytest.raises(ValueError, match="needs paper_id"):
        RetrievalQuery(query="q", answerable=True)


def test_hit_rate_counts_answerable_questions_found_within_top_k():
    results = [
        RetrievalResult("a", answerable=True, rank=1),
        RetrievalResult("b", answerable=True, rank=4),
        RetrievalResult("c", answerable=True, rank=None),
        RetrievalResult("d", answerable=False, top_score=0.1, abstained=True),
    ]
    assert hit_rate(results, top_k=3) == pytest.approx(1 / 3)
    assert hit_rate(results, top_k=5) == pytest.approx(2 / 3)


def test_mean_reciprocal_rank_scores_misses_as_zero_and_ignores_unanswerable():
    results = [
        RetrievalResult("a", answerable=True, rank=1),
        RetrievalResult("b", answerable=True, rank=2),
        RetrievalResult("c", answerable=True, rank=None),
        RetrievalResult("d", answerable=False, top_score=0.1, abstained=True),
    ]
    assert mean_reciprocal_rank(results) == pytest.approx((1 + 0.5 + 0) / 3)


def test_abstain_rate_counts_unanswerable_questions_below_the_threshold():
    results = [
        RetrievalResult("a", answerable=False, top_score=0.3, abstained=True),
        RetrievalResult("b", answerable=False, top_score=0.8, abstained=False),
        RetrievalResult("c", answerable=True, rank=1),
    ]
    assert abstain_rate(results) == pytest.approx(1 / 2)


def test_metrics_return_zero_when_there_are_no_questions_of_that_kind():
    assert hit_rate([], top_k=3) == 0.0
    assert mean_reciprocal_rank([]) == 0.0
    assert abstain_rate([]) == 0.0

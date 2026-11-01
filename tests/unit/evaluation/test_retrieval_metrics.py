import math

import pytest

from steel_slag_slm.config import EvalQuestion, EvidenceSpan
from steel_slag_slm.evaluation.retrieval_metrics import (
    hit_at_k,
    matches,
    mean_scores,
    missing_evidence,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
    score_question,
)
from steel_slag_slm.schemas import Chunk


def _chunk(text: str, paper_id: str = "W1", kind: str = "text") -> Chunk:
    return Chunk(paper_id=paper_id, kind=kind, section="", text=text, position=0)


def _question(*evidence: EvidenceSpan) -> EvalQuestion:
    return EvalQuestion(id="q1", query="q", evidence=list(evidence))


STRENGTH = EvidenceSpan(paper_id="W1", kind="text", text="51.9 MPa after 28 days", grade=2)
PARTIAL = EvidenceSpan(paper_id="W2", kind="table", text="compressive strength", grade=1)


def _ranked_with_answer_at(rank: int, total: int = 20) -> list[Chunk]:
    ranked = [_chunk(f"unrelated passage {i}") for i in range(total)]
    ranked[rank - 1] = _chunk("The concrete reached 51.9 MPa after 28 days of curing.")
    return ranked


def test_match_needs_the_paper_kind_and_text_ignoring_case_and_spacing():
    assert matches(_chunk("reached 51.9  MPa\nafter 28 DAYS"), STRENGTH)
    assert not matches(_chunk("51.9 MPa after 28 days", paper_id="W2"), STRENGTH)
    assert not matches(_chunk("51.9 MPa after 28 days", kind="table"), STRENGTH)


def test_answer_at_rank_7_misses_top_5_but_is_found_in_top_20():
    question = _question(STRENGTH)
    ranked = _ranked_with_answer_at(7)
    assert hit_at_k(ranked, question, 5) == 0
    assert recall_at_k(ranked, question, 5) == 0
    assert hit_at_k(ranked, question, 20) == 1
    assert recall_at_k(ranked, question, 20) == 1
    assert reciprocal_rank(ranked, question) == pytest.approx(1 / 7)


def test_recall_is_the_share_of_evidence_items_found():
    question = _question(STRENGTH, PARTIAL)
    ranked = _ranked_with_answer_at(1)
    assert recall_at_k(ranked, question, 5) == pytest.approx(0.5)


def test_ndcg_is_one_when_the_best_evidence_is_ranked_first():
    question = _question(STRENGTH)
    assert ndcg_at_k(_ranked_with_answer_at(1), question, 10) == pytest.approx(1.0)


def test_ndcg_falls_with_lower_rank_and_ignores_results_beyond_k():
    question = _question(STRENGTH)
    at_3 = ndcg_at_k(_ranked_with_answer_at(3), question, 10)
    assert at_3 == pytest.approx(1 / math.log2(4))
    assert ndcg_at_k(_ranked_with_answer_at(12), question, 10) == 0


def test_ndcg_rewards_ranking_full_answers_above_partial_ones():
    question = _question(STRENGTH, PARTIAL)
    full = _chunk("reached 51.9 MPa after 28 days")
    partial = _chunk("compressive strength by age", paper_id="W2", kind="table")
    assert ndcg_at_k([full, partial], question, 10) == pytest.approx(1.0)
    assert ndcg_at_k([partial, full], question, 10) < 1.0


def test_missing_evidence_lists_labels_no_chunk_contains():
    question = _question(STRENGTH, PARTIAL)
    assert missing_evidence([_chunk("reached 51.9 MPa after 28 days")], question) == [PARTIAL]


def test_mean_scores_averages_each_metric():
    question = _question(STRENGTH)
    scores = [
        score_question(_ranked_with_answer_at(1), question),
        score_question(_ranked_with_answer_at(7), question),
    ]
    means = mean_scores(scores)
    assert means["hit_at_5"] == pytest.approx(0.5)
    assert means["recall_at_20"] == pytest.approx(1.0)
    assert means["reciprocal_rank"] == pytest.approx((1 + 1 / 7) / 2)


def test_ndcg_credits_each_evidence_once_even_if_several_chunks_contain_it():
    question = _question(STRENGTH)
    repeated = [_chunk("reached 51.9 MPa after 28 days") for _ in range(3)]
    assert ndcg_at_k(repeated, question, 10) == pytest.approx(1.0)

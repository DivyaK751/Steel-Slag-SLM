"""Score a ranked list of chunks against the evidence a test question is known to have.

Evidence is matched by text rather than by chunk ID, so the labels still work after the index
is rebuilt with different chunk boundaries.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from steel_slag_slm.config import EvalQuestion, EvidenceSpan
from steel_slag_slm.schemas import Chunk


@dataclass(frozen=True, slots=True)
class QuestionScores:
    """Retrieval scores for one test question."""

    question_id: str
    hit_at_5: float
    hit_at_20: float
    recall_at_5: float
    recall_at_20: float
    reciprocal_rank: float
    ndcg_at_10: float


def _normalize(text: str) -> str:
    return " ".join(text.split()).lower()


def matches(chunk: Chunk, evidence: EvidenceSpan) -> bool:
    """True when the chunk is from the evidence's paper and kind and contains its text."""
    return (
        chunk.paper_id == evidence.paper_id
        and chunk.kind == evidence.kind
        and _normalize(evidence.text) in _normalize(chunk.text)
    )


def chunk_grade(chunk: Chunk, question: EvalQuestion) -> int:
    """The highest grade of any evidence the chunk contains; 0 if it contains none."""
    return max((e.grade for e in question.evidence if matches(chunk, e)), default=0)


def hit_at_k(ranked: Sequence[Chunk], question: EvalQuestion, k: int) -> float:
    """1 if any of the top k chunks contains evidence, else 0."""
    return 1.0 if any(chunk_grade(chunk, question) for chunk in ranked[:k]) else 0.0


def recall_at_k(ranked: Sequence[Chunk], question: EvalQuestion, k: int) -> float:
    """Share of the question's evidence items found in the top k chunks."""
    found = sum(1 for e in question.evidence if any(matches(chunk, e) for chunk in ranked[:k]))
    return found / len(question.evidence)


def reciprocal_rank(ranked: Sequence[Chunk], question: EvalQuestion) -> float:
    """1/rank of the first chunk with evidence; 0 when none was retrieved."""
    for rank, chunk in enumerate(ranked, start=1):
        if chunk_grade(chunk, question):
            return 1 / rank
    return 0.0


def _dcg(gains: Sequence[int]) -> float:
    return sum((2**gain - 1) / math.log2(position + 2) for position, gain in enumerate(gains))


def _gains(ranked: Sequence[Chunk], question: EvalQuestion) -> list[int]:
    """Grade earned at each rank, crediting each evidence item once, at its first match.

    A table split into row groups repeats its caption in every piece, so without this the same
    evidence would be counted several times and nDCG could exceed 1.
    """
    credited: set[int] = set()
    gains: list[int] = []
    for chunk in ranked:
        open_matches = [
            (e.grade, i)
            for i, e in enumerate(question.evidence)
            if i not in credited and matches(chunk, e)
        ]
        if not open_matches:
            gains.append(0)
            continue
        grade, index = max(open_matches)
        credited.add(index)
        gains.append(grade)
    return gains


def ndcg_at_k(ranked: Sequence[Chunk], question: EvalQuestion, k: int) -> float:
    """Graded ranking quality of the top k: 1 means the best evidence is ranked first."""
    ideal = _dcg(sorted((e.grade for e in question.evidence), reverse=True)[:k])
    if ideal == 0:
        return 0.0
    return _dcg(_gains(ranked[:k], question)) / ideal


def score_question(ranked: Sequence[Chunk], question: EvalQuestion) -> QuestionScores:
    """All metrics for one question, from its ranked results (best first)."""
    return QuestionScores(
        question_id=question.id,
        hit_at_5=hit_at_k(ranked, question, 5),
        hit_at_20=hit_at_k(ranked, question, 20),
        recall_at_5=recall_at_k(ranked, question, 5),
        recall_at_20=recall_at_k(ranked, question, 20),
        reciprocal_rank=reciprocal_rank(ranked, question),
        ndcg_at_10=ndcg_at_k(ranked, question, 10),
    )


def mean_scores(scores: Sequence[QuestionScores]) -> dict[str, float]:
    """Average each metric over the questions."""
    names = [
        "hit_at_5",
        "hit_at_20",
        "recall_at_5",
        "recall_at_20",
        "reciprocal_rank",
        "ndcg_at_10",
    ]
    if not scores:
        return dict.fromkeys(names, 0.0)
    return {name: sum(getattr(s, name) for s in scores) / len(scores) for name in names}


def missing_evidence(chunks: Sequence[Chunk], question: EvalQuestion) -> list[EvidenceSpan]:
    """Evidence that no chunk in the index contains, usually a label gone stale after a rebuild."""
    return [e for e in question.evidence if not any(matches(chunk, e) for chunk in chunks)]

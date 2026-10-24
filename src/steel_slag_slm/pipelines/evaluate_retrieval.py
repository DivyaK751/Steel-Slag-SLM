"""Score the saved index against the fixed retrieval test set."""

from dataclasses import dataclass
from typing import Any

from steel_slag_slm.config import EmbeddingSettings, EvidenceSpan, RetrievalEvalSet
from steel_slag_slm.data import embedding_store
from steel_slag_slm.evaluation.retrieval_metrics import (
    QuestionScores,
    missing_evidence,
    score_question,
)
from steel_slag_slm.rag.embeddings import TextEncoder
from steel_slag_slm.rag.search import search_store


@dataclass(frozen=True, slots=True)
class RetrievalEvalReport:
    """Per-question scores, plus evidence the index no longer contains."""

    scores: list[QuestionScores]
    missing: dict[str, list[EvidenceSpan]]  # question id -> stale evidence


def run_retrieval_eval(
    settings: EmbeddingSettings,
    eval_set: RetrievalEvalSet,
    drive_service: Any,
    parsed_folder_id: str,
    encoder: TextEncoder,
) -> RetrievalEvalReport:
    """Ask every test question and score the top results against its known evidence."""
    store = embedding_store.load_store(
        drive_service, parsed_folder_id, settings.model, encoder.dimension
    )
    scores: list[QuestionScores] = []
    missing: dict[str, list[EvidenceSpan]] = {}
    for question in eval_set.questions:
        hits = search_store(store, encoder, question.query, eval_set.depth, settings.query_prefix)
        scores.append(score_question([hit.chunk for hit in hits], question))
        stale = missing_evidence(store.chunks, question)
        if stale:
            missing[question.id] = stale
    return RetrievalEvalReport(scores=scores, missing=missing)

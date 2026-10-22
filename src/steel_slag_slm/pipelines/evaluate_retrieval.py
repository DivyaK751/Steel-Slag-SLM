"""Run the labelled questions against the saved index and score the results."""

from typing import Any

from steel_slag_slm.config import EmbeddingSettings, RetrievalEvalSettings
from steel_slag_slm.data import embedding_store
from steel_slag_slm.evaluation.retrieval_metrics import RetrievalResult, first_relevant_rank
from steel_slag_slm.rag.embeddings import TextEncoder
from steel_slag_slm.rag.search import search_store


def run_retrieval_eval(
    settings: EmbeddingSettings,
    evaluation: RetrievalEvalSettings,
    drive_service: Any,
    parsed_folder_id: str,
    encoder: TextEncoder,
) -> list[RetrievalResult]:
    """Ask every question and record where its answer ranked, or how low the best score was."""
    store = embedding_store.load_store(
        drive_service, parsed_folder_id, settings.model, encoder.dimension
    )
    results: list[RetrievalResult] = []
    for question in evaluation.queries:
        hits = search_store(store, encoder, question.query, evaluation.top_k, settings.query_prefix)
        if question.answerable:
            rank = first_relevant_rank([hit.chunk for hit in hits], question)
            results.append(RetrievalResult(query=question.query, answerable=True, rank=rank))
            continue
        top_score = hits[0].score if hits else None
        abstained = top_score is None or top_score < evaluation.abstain_below_score
        results.append(
            RetrievalResult(
                query=question.query,
                answerable=False,
                top_score=top_score,
                abstained=abstained,
            )
        )
    return results

"""Search the saved index on Drive with the two-stage retriever."""

from typing import Any

from steel_slag_slm.config import EmbeddingSettings
from steel_slag_slm.data import embedding_store
from steel_slag_slm.rag.embeddings import TextEncoder
from steel_slag_slm.rag.retrieval import PassageScorer, TwoStageRetriever
from steel_slag_slm.rag.search import SearchHit
from steel_slag_slm.schemas import ChunkKind

# Kind filters are applied to this many reranked chunks, so a filter is rarely starved
FILTER_DEPTH = 100


def run_search(
    settings: EmbeddingSettings,
    drive_service: Any,
    store_folder_id: str,
    encoder: TextEncoder,
    scorer: PassageScorer,
    query: str,
    top_k: int | None = None,
    kinds: set[ChunkKind] | None = None,
) -> list[SearchHit]:
    """Load the index saved under store_folder_id and return the chunks that best answer the
    query."""
    store = embedding_store.load_store(
        drive_service, store_folder_id, settings.model, encoder.dimension
    )
    retriever = TwoStageRetriever(
        store, encoder, scorer, settings.query_prefix, settings.top_papers, settings.candidates
    )
    top_k = top_k or settings.top_k
    if kinds is None:
        return retriever.search(query, top_k)
    hits = retriever.search(query, FILTER_DEPTH)
    return [hit for hit in hits if hit.chunk.kind in kinds][:top_k]

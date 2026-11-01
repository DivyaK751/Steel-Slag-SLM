"""Run a semantic search over the saved index on Drive."""

from typing import Any

from steel_slag_slm.config import EmbeddingSettings
from steel_slag_slm.data import embedding_store
from steel_slag_slm.rag.embeddings import TextEncoder
from steel_slag_slm.rag.search import SearchHit, search_store
from steel_slag_slm.schemas import ChunkKind


def run_search(
    settings: EmbeddingSettings,
    drive_service: Any,
    parsed_folder_id: str,
    encoder: TextEncoder,
    query: str,
    top_k: int | None = None,
    kinds: set[ChunkKind] | None = None,
) -> list[SearchHit]:
    """Load the saved index from Drive and return the chunks closest to the query."""
    store = embedding_store.load_store(
        drive_service, parsed_folder_id, settings.model, encoder.dimension
    )
    return search_store(
        store,
        encoder,
        query,
        top_k or settings.top_k,
        settings.query_prefix,
        kinds,
    )

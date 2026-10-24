"""Semantic search over a VectorStore."""

from collections.abc import Collection
from dataclasses import dataclass

from steel_slag_slm.rag.embeddings import TextEncoder
from steel_slag_slm.rag.vector_store import VectorStore
from steel_slag_slm.schemas import Chunk, ChunkKind


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One retrieved chunk and its similarity to the query (higher is closer)."""

    score: float
    chunk: Chunk


def search_store(
    store: VectorStore,
    encoder: TextEncoder,
    query: str,
    top_k: int,
    query_prefix: str,
    kinds: Collection[ChunkKind] | None = None,
    paper_id: str | None = None,
) -> list[SearchHit]:
    """Return the top_k chunks closest to the query, optionally only of the given kinds or
    from one paper.

    Raises:
        ValueError: If the query is blank or top_k is not positive.
    """
    if not query.strip():
        raise ValueError("query must not be blank")
    if top_k <= 0:
        raise ValueError("top_k must be greater than zero")

    query_vector = encoder.encode([f"{query_prefix}{query}"])[0]
    if kinds is None and paper_id is None:
        pairs = store.search(query_vector, top_k)
    else:
        # Filter after the search, over every vector, so a filter is never starved of hits
        pairs = [
            (score, chunk)
            for score, chunk in store.search(query_vector, store.size)
            if (kinds is None or chunk.kind in kinds)
            and (paper_id is None or chunk.paper_id == paper_id)
        ][:top_k]
    return [SearchHit(score=score, chunk=chunk) for score, chunk in pairs]

import pytest

from steel_slag_slm.rag.search import search_store
from steel_slag_slm.rag.vector_store import VectorStore
from steel_slag_slm.schemas import Chunk


def _store(encoder) -> VectorStore:  # encoder is the keyword_encoder fixture
    chunks = [
        Chunk("W1", "text", "Results", "Free lime drives volume change.", 0),
        Chunk("W1", "table", "Results", "Table 1. Sinter composition", 1),
        Chunk("W2", "text", "Intro", "Blended cement uses slag.", 2),
    ]
    store = VectorStore.empty(encoder.dimension)
    store.add(chunks, encoder.encode([chunk.text for chunk in chunks]))
    return store


def test_search_ranks_the_chunk_that_mentions_the_query_first(keyword_encoder):
    hits = search_store(_store(keyword_encoder), keyword_encoder, "lime", 2, query_prefix="")
    assert hits[0].chunk.text == "Free lime drives volume change."
    assert hits[0].score > hits[1].score


def test_search_prepends_the_query_prefix_before_encoding(keyword_encoder):
    store = _store(keyword_encoder)
    search_store(store, keyword_encoder, "cement", 1, query_prefix="Find: ")
    assert keyword_encoder.seen[-1] == "Find: cement"


def test_search_can_keep_only_some_kinds_of_chunk(keyword_encoder):
    hits = search_store(
        _store(keyword_encoder), keyword_encoder, "sinter", 3, query_prefix="", kinds={"text"}
    )
    assert all(hit.chunk.kind == "text" for hit in hits)
    assert len(hits) == 2


def test_search_can_keep_only_one_paper(keyword_encoder):
    hits = search_store(
        _store(keyword_encoder), keyword_encoder, "cement", 3, query_prefix="", paper_id="W1"
    )
    assert [hit.chunk.paper_id for hit in hits] == ["W1", "W1"]


def test_search_rejects_a_blank_query(keyword_encoder):
    with pytest.raises(ValueError, match="blank"):
        search_store(_store(keyword_encoder), keyword_encoder, "   ", 1, "")

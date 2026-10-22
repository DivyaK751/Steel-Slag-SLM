import numpy as np
import pytest

from steel_slag_slm.rag.vector_store import VectorStore
from steel_slag_slm.schemas import Chunk


def _chunk(text: str) -> Chunk:
    return Chunk(paper_id="W1", kind="text", section="", text=text, position=0)


def _store() -> VectorStore:
    store = VectorStore.empty(dimension=2)
    vectors = np.array([[1.0, 0.0], [0.0, 1.0], [0.6, 0.8]], dtype=np.float32)
    store.add([_chunk("east"), _chunk("north"), _chunk("mostly east")], vectors)
    return store


def test_search_returns_the_closest_chunks_first() -> None:
    results = _store().search(np.array([1.0, 0.0], dtype=np.float32), top_k=2)
    assert [chunk.text for _, chunk in results] == ["east", "mostly east"]
    assert results[0][0] == pytest.approx(1.0)


def test_search_on_an_empty_store_returns_nothing() -> None:
    assert VectorStore.empty(dimension=2).search(np.array([1.0, 0.0]), top_k=3) == []


def test_add_rejects_vectors_of_the_wrong_shape() -> None:
    store = VectorStore.empty(dimension=2)
    with pytest.raises(ValueError, match="expected vectors"):
        store.add([_chunk("a")], np.zeros((1, 3), dtype=np.float32))


def test_constructor_rejects_an_index_and_chunks_that_disagree() -> None:
    store = _store()
    with pytest.raises(ValueError, match="index holds 3 vectors"):
        VectorStore(store._index, [_chunk("only one")])


def test_index_bytes_round_trip_keeps_results_and_chunks() -> None:
    store = _store()
    restored = VectorStore.from_index_bytes(store.to_index_bytes(), store.chunks)
    query = np.array([0.0, 1.0], dtype=np.float32)
    assert [chunk.text for _, chunk in restored.search(query, top_k=1)] == ["north"]
    assert restored.size == 3


def test_search_rejects_a_non_positive_top_k() -> None:
    with pytest.raises(ValueError, match="top_k"):
        _store().search(np.array([1.0, 0.0]), top_k=0)

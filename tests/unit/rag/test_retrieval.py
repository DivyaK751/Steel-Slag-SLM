from collections.abc import Sequence

import numpy as np
import pytest

from steel_slag_slm.rag.retrieval import TwoStageRetriever, keyword_tokens
from steel_slag_slm.rag.vector_store import VectorStore
from steel_slag_slm.schemas import Chunk


class WordScorer:
    """Scores a passage by how many of the query's words it contains; records what it read."""

    def __init__(self) -> None:
        self.read: list[str] = []

    def score(self, query: str, passages: Sequence[str]) -> np.ndarray:
        self.read.extend(passages)
        words = set(keyword_tokens(query))
        return np.array([len(words & set(keyword_tokens(p))) for p in passages], dtype=float)


CHUNKS = [
    Chunk("W1", "text", "Intro", "Free lime in BOF slag causes expansion.", 0, paper_title="BOF"),
    Chunk("W1", "table", "", "Table 1\n| CaO | 10.6 |", 1, paper_title="Free lime of BOF slag"),
    Chunk("W2", "text", "Intro", "Cement made with sinter dust.", 0, paper_title="Sinter"),
    Chunk("W3", "text", "Intro", "Lime kilns and cement.", 0, paper_title="Kilns"),
]


def _retriever(encoder, scorer, top_papers: int) -> TwoStageRetriever:
    store = VectorStore.empty(encoder.dimension)
    store.add(CHUNKS, encoder.encode([chunk.text for chunk in CHUNKS]))
    return TwoStageRetriever(store, encoder, scorer, "", top_papers, candidates=10)


def test_search_reranks_every_chunk_of_the_papers_stage_one_finds(keyword_encoder):
    scorer = WordScorer()
    hits = _retriever(keyword_encoder, scorer, top_papers=1).search("free lime bof", top_k=5)
    assert [(h.chunk.paper_id, h.chunk.position) for h in hits][0][0] == "W1"
    assert {h.chunk.paper_id for h in hits} == {"W1"}
    assert len(scorer.read) == 2  # both W1 chunks, nothing from W2 or W3


def test_the_reranker_reads_each_chunks_paper_title_first(keyword_encoder):
    scorer = WordScorer()
    _retriever(keyword_encoder, scorer, top_papers=1).search("free lime bof", top_k=5)
    assert scorer.read[0].startswith("BOF\n")


def test_search_can_be_limited_to_one_paper(keyword_encoder):
    hits = _retriever(keyword_encoder, WordScorer(), top_papers=3).search(
        "lime cement", top_k=5, paper_id="W3"
    )
    assert [h.chunk.paper_id for h in hits] == ["W3"]


def test_search_rejects_a_blank_query(keyword_encoder):
    with pytest.raises(ValueError, match="blank"):
        _retriever(keyword_encoder, WordScorer(), top_papers=1).search("  ", top_k=5)

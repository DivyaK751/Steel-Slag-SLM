"""Two-stage retrieval: hybrid search finds the papers, a reranker orders their passages.

Stage 1 fuses the embedding ranking with a BM25 keyword ranking (reciprocal rank fusion) and
keeps the papers of the best-ranked chunks. Stage 2 has a cross-encoder read the question with
every chunk of those papers, each preceded by its paper's title, and sorts them by its score.
"""

import re
from collections.abc import Sequence
from typing import Protocol

import numpy as np
from rank_bm25 import BM25Okapi

from steel_slag_slm.rag.chunking import passage_for_embedding, passage_for_reranking
from steel_slag_slm.rag.embeddings import TextEncoder
from steel_slag_slm.rag.search import SearchHit
from steel_slag_slm.rag.vector_store import VectorStore

RRF_K = 60  # the usual constant of reciprocal rank fusion; damps the weight of the top ranks
_TOKEN = re.compile(r"[a-z0-9]+(?:[.,][0-9]+)?")


def keyword_tokens(text: str) -> list[str]:
    """Lowercase words and numbers, keeping decimals such as "10.6" whole."""
    return _TOKEN.findall(text.lower())


class PassageScorer(Protocol):
    """Anything that scores how well each passage answers a question (higher is better)."""

    def score(self, query: str, passages: Sequence[str]) -> np.ndarray: ...


class CrossEncoderScorer:
    """A sentence-transformers cross-encoder, on the GPU in half precision when there is one."""

    def __init__(self, model_name: str) -> None:
        # Imported here: torch is slow to load and only this class needs it
        import torch
        from sentence_transformers import CrossEncoder

        device = "cuda" if torch.cuda.is_available() else "cpu"
        self._model = CrossEncoder(model_name, max_length=512, device=device)
        if device == "cuda":
            self._model.model.half()

    def score(self, query: str, passages: Sequence[str]) -> np.ndarray:
        pairs = [(query, passage) for passage in passages]
        return np.asarray(self._model.predict(pairs, batch_size=64), dtype=np.float32)


class TwoStageRetriever:
    """Search over one store: find the top papers, then rerank all of their chunks."""

    def __init__(
        self,
        store: VectorStore,
        encoder: TextEncoder,
        scorer: PassageScorer,
        query_prefix: str,
        top_papers: int,
        candidates: int,
    ) -> None:
        self._store = store
        self._encoder = encoder
        self._scorer = scorer
        self._query_prefix = query_prefix
        self._top_papers = top_papers
        self._candidates = candidates
        self._chunks = store.chunks
        self._rows = {(c.paper_id, c.position): row for row, c in enumerate(self._chunks)}
        tokens = [keyword_tokens(passage_for_embedding(c)) for c in self._chunks]
        self._bm25 = BM25Okapi(tokens) if tokens else None

    def search(self, query: str, top_k: int, paper_id: str | None = None) -> list[SearchHit]:
        """Return the top_k chunks for the query, best first; only paper_id's when given.

        Raises:
            ValueError: If the query is blank or top_k is not positive.
        """
        if not query.strip():
            raise ValueError("query must not be blank")
        if top_k <= 0:
            raise ValueError("top_k must be greater than zero")
        if self._bm25 is None:
            return []
        papers = {paper_id} if paper_id else self.find_papers(query)
        rows = [row for row, chunk in enumerate(self._chunks) if chunk.paper_id in papers]
        if not rows:
            return []
        scores = self._scorer.score(
            query, [passage_for_reranking(self._chunks[row]) for row in rows]
        )
        best = np.argsort(-scores)[:top_k]
        return [SearchHit(score=float(scores[i]), chunk=self._chunks[rows[i]]) for i in best]

    def find_papers(self, query: str) -> set[str]:
        """Return the papers of the best chunks in the fused embedding and keyword rankings."""
        if self._bm25 is None:
            return set()
        vector = self._encoder.encode([f"{self._query_prefix}{query}"])[0]
        dense = [
            self._rows[(chunk.paper_id, chunk.position)]
            for _, chunk in self._store.search(vector, self._candidates)
        ]
        keyword = np.argsort(-self._bm25.get_scores(keyword_tokens(query)))[: self._candidates]
        fused: dict[int, float] = {}
        for ranking in (dense, keyword):
            for rank, row in enumerate(ranking):
                fused[int(row)] = fused.get(int(row), 0.0) + 1 / (RRF_K + rank + 1)
        papers: list[str] = []
        for row in sorted(fused, key=fused.__getitem__, reverse=True):
            if self._chunks[row].paper_id not in papers:
                papers.append(self._chunks[row].paper_id)
            if len(papers) == self._top_papers:
                break
        return set(papers)

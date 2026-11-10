import json
from collections.abc import Sequence

import numpy as np
import pytest

from steel_slag_slm.config import EmbeddingSettings, EvalQuestion, EvidenceSpan, RetrievalEvalSet
from steel_slag_slm.pipelines.evaluate_retrieval import (
    format_retrieval_report,
    run_corpus_eval,
    run_retrieval_eval,
)
from steel_slag_slm.rag.retrieval import TwoStageRetriever, keyword_tokens
from steel_slag_slm.rag.vector_store import VectorStore
from steel_slag_slm.schemas import Chunk

SETTINGS = EmbeddingSettings(
    model="model-a",
    query_prefix="",
    chunk_max_chars=800,
    table_max_chars=1500,
    papers_per_save=10,
    top_k=3,
    index_folder_name="_search_index",
    reranker="reranker-a",
    top_papers=5,
    candidates=10,
)
CHUNKS = [
    Chunk("W1", "text", "Results", "Free lime causes volume expansion.", 0),
    Chunk("W2", "text", "Intro", "Blended cement uses slag.", 1),
]


class WordScorer:
    """Scores a passage by how many of the query's words it contains."""

    def score(self, query: str, passages: Sequence[str]) -> np.ndarray:
        words = set(keyword_tokens(query))
        return np.array([len(words & set(keyword_tokens(p))) for p in passages], dtype=float)


def _store(encoder) -> VectorStore:
    store = VectorStore.empty(encoder.dimension)
    store.add(CHUNKS, encoder.encode([chunk.text for chunk in CHUNKS]))
    return store


def _question(qid: str, query: str, paper: str, text: str) -> EvalQuestion:
    return EvalQuestion(
        id=qid, query=query, evidence=[EvidenceSpan(paper_id=paper, kind="text", text=text)]
    )


def test_run_retrieval_eval_scores_questions_and_flags_stale_labels(keyword_encoder):
    store = _store(keyword_encoder)
    retriever = TwoStageRetriever(store, keyword_encoder, WordScorer(), "", 5, 10)
    eval_set = RetrievalEvalSet(
        depth=20,
        questions=[
            _question("lime", "free lime", "W1", "free lime causes"),
            _question("gone", "cement", "W9", "no longer indexed"),
        ],
    )

    report = run_retrieval_eval(eval_set, retriever, store)

    lime, gone = report.scores
    assert (lime.hit_at_5, lime.reciprocal_rank, gone.hit_at_20) == (1, 1, 0)
    assert list(report.missing) == ["gone"]
    assert report.rankings["lime"][0].paper_id == "W1"


def test_run_retrieval_eval_searches_only_the_sets_paper(keyword_encoder):
    store = _store(keyword_encoder)
    retriever = TwoStageRetriever(store, keyword_encoder, WordScorer(), "", 5, 10)
    eval_set = RetrievalEvalSet(
        depth=20, paper_id="W2", questions=[_question("lime", "lime", "W1", "free lime causes")]
    )

    report = run_retrieval_eval(eval_set, retriever, store)

    # The only answer is in W1, which a search limited to W2 must not return
    assert report.scores[0].hit_at_20 == 0


def _paper(paper_id: str, text: str) -> bytes:
    return json.dumps({"openalex_id": paper_id, "markdown": text, "tables": []}).encode()


def test_run_corpus_eval_searches_only_the_papers_the_corpus_file_lists(tmp_path, keyword_encoder):
    papers = tmp_path / "papers"
    papers.mkdir()
    (papers / "W1.json").write_bytes(_paper("W1", "Free lime causes volume expansion."))
    (papers / "W2.json").write_bytes(_paper("W2", "Free lime is also discussed here."))
    (tmp_path / "corpus.txt").write_text("W2\n", encoding="utf-8")
    (tmp_path / "set.yaml").write_text(
        "depth: 20\ncorpus: corpus.txt\nquestions:\n"
        "  - {id: q1, query: free lime, answer: ['volume expansion'],\n"
        "     evidence: [{paper_id: W1, kind: text, text: volume expansion}]}\n",
        encoding="utf-8",
    )

    eval_set, report = run_corpus_eval(
        tmp_path / "set.yaml", papers, {}, SETTINGS, keyword_encoder, WordScorer()
    )

    assert {chunk.paper_id for chunk in report.rankings["q1"]} == {"W2"}
    assert "q1 [text] free lime" in format_retrieval_report(eval_set, report)


def test_run_corpus_eval_needs_a_corpus_file(tmp_path, keyword_encoder):
    (tmp_path / "set.yaml").write_text("depth: 20\nquestions: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no corpus"):
        run_corpus_eval(
            tmp_path / "set.yaml", tmp_path, {}, SETTINGS, keyword_encoder, WordScorer()
        )

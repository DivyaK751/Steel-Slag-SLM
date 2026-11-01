from steel_slag_slm.config import EmbeddingSettings, EvalQuestion, EvidenceSpan, RetrievalEvalSet
from steel_slag_slm.data import embedding_store
from steel_slag_slm.pipelines.evaluate_retrieval import run_retrieval_eval
from steel_slag_slm.rag.vector_store import VectorStore
from steel_slag_slm.schemas import Chunk

SETTINGS = EmbeddingSettings(
    model="model-a",
    query_prefix="",
    chunk_max_chars=800,
    table_max_chars=1500,
    papers_per_save=10,
    top_k=3,
)
CHUNKS = [
    Chunk("W1", "text", "Results", "Free lime causes volume expansion.", 0),
    Chunk("W2", "text", "Intro", "Blended cement uses slag.", 1),
]


def _save_store(encoder) -> None:
    store = VectorStore.empty(encoder.dimension)
    store.add(CHUNKS, encoder.encode([chunk.text for chunk in CHUNKS]))
    embedding_store.save_store(None, "parsed", "model-a", store)


def test_run_retrieval_eval_scores_questions_and_flags_stale_labels(fake_drive, keyword_encoder):
    _save_store(keyword_encoder)
    eval_set = RetrievalEvalSet(
        depth=20,
        questions=[
            EvalQuestion(
                id="lime",
                query="lime",
                evidence=[EvidenceSpan(paper_id="W1", kind="text", text="free lime causes")],
            ),
            EvalQuestion(
                id="gone",
                query="cement",
                evidence=[EvidenceSpan(paper_id="W9", kind="text", text="no longer indexed")],
            ),
        ],
    )

    report = run_retrieval_eval(SETTINGS, eval_set, None, "parsed", keyword_encoder)

    lime, gone = report.scores
    assert lime.hit_at_5 == 1
    assert lime.reciprocal_rank == 1
    assert gone.hit_at_20 == 0
    assert list(report.missing) == ["gone"]


def test_run_retrieval_eval_searches_only_the_sets_paper(fake_drive, keyword_encoder):
    _save_store(keyword_encoder)
    eval_set = RetrievalEvalSet(
        depth=20,
        paper_id="W2",
        questions=[
            EvalQuestion(
                id="lime",
                query="lime",
                evidence=[EvidenceSpan(paper_id="W1", kind="text", text="free lime causes")],
            )
        ],
    )

    report = run_retrieval_eval(SETTINGS, eval_set, None, "parsed", keyword_encoder)

    # The only answer is in W1, which a search limited to W2 must not return
    assert report.scores[0].hit_at_20 == 0

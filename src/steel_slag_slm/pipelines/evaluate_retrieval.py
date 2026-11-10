"""Score retrieval against a fixed test set, over the saved index or a test set's own papers."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from steel_slag_slm.config import (
    EmbeddingSettings,
    EvidenceSpan,
    RetrievalEvalSet,
    load_retrieval_eval_set,
)
from steel_slag_slm.data.parsed_document import parse_parsed_json
from steel_slag_slm.evaluation.retrieval_metrics import (
    QuestionScores,
    contains_answer,
    mean_scores,
    missing_evidence,
    score_question,
)
from steel_slag_slm.rag.chunking import build_chunks, passage_for_embedding
from steel_slag_slm.rag.embeddings import TextEncoder
from steel_slag_slm.rag.retrieval import PassageScorer, TwoStageRetriever
from steel_slag_slm.rag.search import SearchHit
from steel_slag_slm.rag.vector_store import VectorStore
from steel_slag_slm.schemas import Chunk


class Retriever(Protocol):
    """Anything that returns a question's best chunks, optionally from one paper only."""

    def search(self, query: str, top_k: int, paper_id: str | None = None) -> list[SearchHit]: ...


@dataclass(frozen=True, slots=True)
class RetrievalEvalReport:
    """Per-question scores and rankings, plus evidence the searched chunks no longer contain."""

    scores: list[QuestionScores]
    rankings: dict[str, list[Chunk]]  # question id -> retrieved chunks, best first
    missing: dict[str, list[EvidenceSpan]]  # question id -> stale evidence


def build_corpus_store(
    paper_ids: Sequence[str],
    papers_dir: Path,
    titles: Mapping[str, str],
    settings: EmbeddingSettings,
    encoder: TextEncoder,
) -> VectorStore:
    """Chunk and embed the given papers from local <paper id>.json files into a new store."""
    chunks: list[Chunk] = []
    for paper_id in paper_ids:
        paper = parse_parsed_json((papers_dir / f"{paper_id}.json").read_bytes())
        chunks += build_chunks(
            paper, settings.chunk_max_chars, settings.table_max_chars, titles.get(paper_id, "")
        )
    store = VectorStore.empty(encoder.dimension)
    store.add(chunks, encoder.encode([passage_for_embedding(chunk) for chunk in chunks]))
    return store


def run_retrieval_eval(
    eval_set: RetrievalEvalSet, retriever: Retriever, store: VectorStore
) -> RetrievalEvalReport:
    """Ask every test question and score the top results against its known evidence."""
    scores: list[QuestionScores] = []
    rankings: dict[str, list[Chunk]] = {}
    missing: dict[str, list[EvidenceSpan]] = {}
    for question in eval_set.questions:
        hits = retriever.search(question.query, eval_set.depth, paper_id=eval_set.paper_id)
        rankings[question.id] = [hit.chunk for hit in hits]
        scores.append(score_question(rankings[question.id], question))
        stale = missing_evidence(store.chunks, question)
        if stale:
            missing[question.id] = stale
    return RetrievalEvalReport(scores=scores, rankings=rankings, missing=missing)


def run_corpus_eval(
    eval_path: Path,
    papers_dir: Path,
    titles: Mapping[str, str],
    settings: EmbeddingSettings,
    encoder: TextEncoder,
    scorer: PassageScorer,
) -> tuple[RetrievalEvalSet, RetrievalEvalReport]:
    """Score the two-stage retriever on a test set over the papers its corpus file lists.

    Raises:
        ValueError: If the test set names no corpus file.
    """
    eval_set = load_retrieval_eval_set(eval_path)
    if eval_set.corpus is None:
        raise ValueError(f"{eval_path.name} names no corpus file to search")
    paper_ids = (eval_path.parent / eval_set.corpus).read_text(encoding="utf-8").split()
    store = build_corpus_store(paper_ids, papers_dir, titles, settings, encoder)
    retriever = TwoStageRetriever(
        store,
        encoder,
        scorer,
        settings.query_prefix,
        settings.top_papers,
        settings.candidates,
    )
    return eval_set, run_retrieval_eval(eval_set, retriever, store)


def format_retrieval_report(eval_set: RetrievalEvalSet, report: RetrievalEvalReport) -> str:
    """Return the averages, the answer@5 split by where answers live, and every miss with the
    top 3 results, as plain text."""
    means = mean_scores(report.scores)
    lines = [
        f"paper@5 {means['paper_hit_at_5']:.2f} | answer@5 {means['answer_hit_at_5']:.2f}  "
        f"answer@10 {means['answer_hit_at_10']:.2f}  answer@20 {means['answer_hit_at_20']:.2f} "
        f"| exact label@5 {means['hit_at_5']:.2f}"
    ]
    by_kind: dict[str, list[float]] = {}
    for question, score in zip(eval_set.questions, report.scores, strict=True):
        by_kind.setdefault(question.evidence[0].kind, []).append(score.answer_hit_at_5)
    lines.append(
        "answer@5 by kind: "
        + "  ".join(f"{k} {sum(v) / len(v):.2f} ({len(v)})" for k, v in sorted(by_kind.items()))
    )
    for question, score in zip(eval_set.questions, report.scores, strict=True):
        if score.answer_hit_at_5:
            continue
        ranked = report.rankings[question.id]
        rank = next(
            (i for i, chunk in enumerate(ranked, 1) if contains_answer(chunk, question)), None
        )
        paper = question.evidence[0].paper_id
        lines.append(
            f"\n{question.id} [{question.evidence[0].kind}] {question.query}\n"
            f"   wants {question.answer} from {paper}; answer first at rank {rank or 'none'}"
        )
        for position, chunk in enumerate(ranked[:3], start=1):
            where = "same paper" if chunk.paper_id == paper else f"other {chunk.paper_id}"
            text = " ".join(chunk.text.split())[:220]
            lines.append(f"   {position}. {where}, {chunk.kind}: {text!r}")
    for question_id, stale in report.missing.items():
        for evidence in stale:
            label = evidence.location or evidence.text
            lines.append(
                f"STALE LABEL {question_id}: {evidence.paper_id} {evidence.kind} {label!r}"
            )
    return "\n".join(lines)

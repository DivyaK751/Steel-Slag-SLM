"""Compare existing table chunks with the same corpus augmented by contextual rows."""

import logging
import re
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Literal

from pydantic import BaseModel
from transformers import AutoTokenizer

from steel_slag_slm.config import EmbeddingSettings, EvalQuestion, RetrievalEvalSet
from steel_slag_slm.data.pdf_parsing import extract_content_blocks
from steel_slag_slm.data.table_evidence import (
    RenderingIssue,
    TableGeometryError,
    render_table_evidence,
)
from steel_slag_slm.data.table_inspection import capture_tables
from steel_slag_slm.evaluation.retrieval_metrics import contains_answer, matches, mean_scores
from steel_slag_slm.pipelines.capture_table_corpus import FullDocumentCapture
from steel_slag_slm.pipelines.evaluate_retrieval import run_retrieval_eval
from steel_slag_slm.rag.chunking import build_chunks, passage_for_embedding
from steel_slag_slm.rag.embeddings import Embedder, TextEncoder
from steel_slag_slm.rag.retrieval import CrossEncoderScorer, PassageScorer, TwoStageRetriever
from steel_slag_slm.rag.table_budgeting import (
    BudgetedTable,
    PassageTokenizer,
    TableTokenBudget,
    budget_table_rows,
)
from steel_slag_slm.rag.vector_store import VectorStore
from steel_slag_slm.schemas import Chunk, ParsedPaper

logger = logging.getLogger(__name__)


class PaperRows(BaseModel):
    """Store exact contextual chunks and source-cell mappings from one input capture."""

    capture_sha256: str
    source_sha256: str
    tables: list[BudgetedTable]


class VariantResult(BaseModel):
    """Keep automatic retrieval proxies and full ranked text for condition review."""

    chunk_count: int
    elapsed_seconds: float
    metrics: dict[str, dict[str, float]]
    question_scores: dict[str, dict[str, float]]
    rankings: dict[str, list[Chunk]]
    missing_evidence_question_ids: list[str]


class TableRowEvaluation(BaseModel):
    """Record the paired run with its exact question and candidate inputs."""

    schema_version: Literal[1] = 1
    settings: EmbeddingSettings
    budget: TableTokenBudget
    depth: int
    questions: list[EvalQuestion]
    excluded_questions: dict[str, str]
    papers: dict[str, PaperRows]
    baseline: VariantResult
    augmented: VariantResult
    elapsed_seconds: float


def _questions(
    eval_set: RetrievalEvalSet,
    paper_ids: list[str],
) -> tuple[RetrievalEvalSet, dict[str, str]]:
    if eval_set.paper_id is not None or eval_set.depth < 20:
        raise ValueError("Use a corpus-wide question set with depth at least 20")
    if len({q.id for q in eval_set.questions}) != len(eval_set.questions):
        raise ValueError("Question IDs must be unique")
    excluded: dict[str, str] = {}
    kept = []
    for question in eval_set.questions:
        if any(e.paper_id not in paper_ids for e in question.evidence):
            excluded[question.id] = "Gold paper outside selected corpus"
        elif any(e.kind == "figure" for e in question.evidence):
            excluded[question.id] = "Figure evidence is outside this text/table experiment"
        else:
            kept.append(question)
    if not kept:
        raise ValueError("No eligible text/table questions for the selected corpus")
    # The explicit benchmark selection replaces the historical distractor corpus.
    return eval_set.model_copy(update={"questions": kept, "corpus": None}), excluded


def _paper_chunks(
    capture: FullDocumentCapture,
    settings: EmbeddingSettings,
    budget: TableTokenBudget,
    embedding: PassageTokenizer,
    reranker: PassageTokenizer,
) -> tuple[list[Chunk], list[BudgetedTable]]:
    title = Path(capture.source_filename).stem.removeprefix(f"{capture.paper_id} - ")
    blocks = extract_content_blocks(capture.document)
    baseline = build_chunks(
        ParsedPaper(capture.paper_id, "", (), blocks),
        settings.chunk_max_chars,
        settings.table_max_chars,
        title,
    )
    tables = []
    position = len(baseline)
    for table in capture_tables(capture.document):
        try:
            evidence = render_table_evidence(table)
        except TableGeometryError as exc:
            logger.warning("%s: %s; retaining original Markdown only", capture.paper_id, exc)
            tables.append(
                BudgetedTable(
                    reference=table.reference,
                    rows=[],
                    issues=[
                        RenderingIssue(reason=str(exc)),
                    ],
                )
            )
            continue
        pages = sorted({p.page_no for p in evidence.provenance})
        if not pages or not set(pages) <= capture.document.pages.keys():
            evidence.issues.append(RenderingIssue(reason="Missing or invalid source page"))
            evidence.rows.clear()
        else:
            pages_text = ", ".join(map(str, pages))
            for row in evidence.rows:
                row.text = f"{row.text}\nSource PDF page(s): {pages_text}"
        budgeted = budget_table_rows(
            evidence,
            capture.paper_id,
            title,
            budget,
            embedding,
            reranker,
            position,
        )
        position += len(budgeted.rows)
        tables.append(budgeted)
    return baseline, tables


def _score(
    store: VectorStore,
    eval_set: RetrievalEvalSet,
    settings: EmbeddingSettings,
    encoder: TextEncoder,
    scorer: PassageScorer,
) -> VariantResult:
    start = perf_counter()
    retriever = TwoStageRetriever(
        store,
        encoder,
        scorer,
        settings.query_prefix,
        settings.top_papers,
        settings.candidates,
    )
    report = run_retrieval_eval(eval_set, retriever, store)
    paired_hits = {
        q.id: float(
            any(
                contains_answer(chunk, q) and any(matches(chunk, e) for e in q.evidence)
                for chunk in report.rankings[q.id][:5]
            )
        )
        for q in eval_set.questions
    }
    question_scores = {}
    for score in report.scores:
        values = asdict(score)
        del values["question_id"]
        question_scores[score.question_id] = values | {
            "evidence_and_value_at_5": paired_hits[score.question_id],
        }
    metrics: dict[str, dict[str, float]] = {}
    for group in ("all", "table", "text"):
        pairs = [
            (q, s)
            for q, s in zip(eval_set.questions, report.scores, strict=True)
            if group == "all" or q.evidence[0].kind == group
        ]
        if pairs:
            metrics[group] = mean_scores([s for _, s in pairs])
            metrics[group]["question_count"] = float(len(pairs))
            metrics[group]["evidence_and_value_at_5"] = sum(
                paired_hits[q.id] for q, _ in pairs
            ) / len(pairs)
    return VariantResult(
        chunk_count=store.size,
        elapsed_seconds=perf_counter() - start,
        metrics=metrics,
        question_scores=question_scores,
        rankings=report.rankings,
        missing_evidence_question_ids=list(report.missing),
    )


def evaluate_table_rows(
    paper_ids: list[str],
    captures_dir: Path,
    eval_set: RetrievalEvalSet,
    settings: EmbeddingSettings,
    budget: TableTokenBudget,
    *,
    encoder: TextEncoder | None = None,
    scorer: PassageScorer | None = None,
) -> TableRowEvaluation:
    """Evaluate an additive variant while keeping baseline chunks and their vectors unchanged."""
    start = perf_counter()
    if not paper_ids or len(set(paper_ids)) != len(paper_ids):
        raise ValueError("Select nonempty, unique paper IDs")
    if any(re.fullmatch(r"W[0-9]+", paper_id) is None for paper_id in paper_ids):
        raise ValueError("Paper IDs must be OpenAlex work IDs (W followed by digits)")
    paths = [captures_dir / f"{paper_id}.json" for paper_id in paper_ids]
    missing = [path.stem for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            f"Missing full-document captures ({len(missing)}): {', '.join(missing)}"
        )
    selected, excluded = _questions(eval_set, paper_ids)
    embedding = AutoTokenizer.from_pretrained(settings.model)
    reranker = AutoTokenizer.from_pretrained(settings.reranker)
    if budget.embedding_limit > min(512, embedding.model_max_length) or budget.reranker_limit > 512:
        raise ValueError(
            "The evaluation budgets must fit the configured model inputs (at most 512)"
        )
    for question in selected.questions:
        if len(reranker.encode(question.query, add_special_tokens=False)) > budget.query_reserve:
            raise ValueError(f"{question.id}: exceeds the reranker query reserve")
    baseline_chunks: list[Chunk] = []
    additions: list[Chunk] = []
    papers: dict[str, PaperRows] = {}
    signatures = set()
    for paper_id, path in zip(paper_ids, paths, strict=True):
        content = path.read_bytes()
        capture = FullDocumentCapture.model_validate_json(content)
        if capture.paper_id != paper_id:
            raise ValueError(f"{path.name}: capture paper ID does not match")
        signatures.add(
            (capture.docling_version, capture.docling_core_version, capture.pipeline_options_sha256)
        )
        if len(signatures) != 1:
            raise ValueError("Selected captures have mixed parser versions or settings")
        baseline, tables = _paper_chunks(capture, settings, budget, embedding, reranker)
        if not baseline:
            raise ValueError(f"{paper_id}: no baseline chunks")
        baseline_chunks.extend(baseline)
        additions.extend(row.chunk for table in tables for row in table.rows)
        papers[paper_id] = PaperRows(
            capture_sha256=sha256(content).hexdigest(),
            source_sha256=capture.source_sha256,
            tables=tables,
        )
    if not additions:
        raise ValueError("No eligible contextual rows; cannot compare identical variants")
    logger.info(
        "Prepared %d baseline chunks and %d added rows", len(baseline_chunks), len(additions)
    )
    logger.info("Loading local retrieval models: %s and %s", settings.model, settings.reranker)
    encoder = encoder if encoder is not None else Embedder(settings.model)
    scorer = scorer if scorer is not None else CrossEncoderScorer(settings.reranker)
    store = VectorStore.empty(encoder.dimension)
    store.add(baseline_chunks, encoder.encode([passage_for_embedding(c) for c in baseline_chunks]))
    logger.info("Scoring baseline for %d questions", len(selected.questions))
    baseline_result = _score(store, selected, settings, encoder, scorer)
    store.add(additions, encoder.encode([passage_for_embedding(c) for c in additions]))
    logger.info("Scoring augmented corpus for the same %d questions", len(selected.questions))
    augmented_result = _score(store, selected, settings, encoder, scorer)
    return TableRowEvaluation(
        settings=settings,
        budget=budget,
        depth=selected.depth,
        questions=selected.questions,
        excluded_questions=excluded,
        papers=papers,
        baseline=baseline_result,
        augmented=augmented_result,
        elapsed_seconds=perf_counter() - start,
    )

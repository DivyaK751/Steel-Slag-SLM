"""Prepare local whole-row passages without loading neural model weights or publishing."""

import logging
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from transformers import AutoTokenizer, PreTrainedTokenizerFast

from steel_slag_slm.data.table_evidence import render_table_evidence
from steel_slag_slm.pipelines.inspect_tables import TableInspectionReport
from steel_slag_slm.rag.table_budgeting import BudgetedTable, TableTokenBudget, budget_table_rows

logger = logging.getLogger(__name__)


class TableChunkReport(BaseModel):
    """Record exact input/tokenizer fingerprints and unreviewed, budgeted row passages."""

    schema_version: Literal[1] = 1
    paper_id: str
    inspection_filename: str
    inspection_sha256: str
    source_sha256: str
    filename_title: str
    embedding_model: str
    reranker_model: str
    embedding_tokenizer_sha256: str
    reranker_tokenizer_sha256: str
    transformers_version: str
    tokenizers_version: str
    budget: TableTokenBudget
    tables: list[BudgetedTable]


def budget_local_table_evidence(
    inspection_dir: Path,
    output_dir: Path,
    budget: TableTokenBudget,
    embedding_model: str,
    reranker_model: str,
) -> list[TableChunkReport]:
    """Re-render inspections and budget rows using cached production-model tokenizers.

    Only tokenizer assets are loaded. All reports are prepared before exclusive writes.
    Filename stems supply provisional titles until registry metadata is available.
    """
    paths = sorted(inspection_dir.glob("*.json"))
    if not paths:
        raise ValueError(f"No inspection JSON reports in {inspection_dir}")
    tokenizers = [
        AutoTokenizer.from_pretrained(name, local_files_only=True)
        for name in (embedding_model, reranker_model)
    ]
    if not all(isinstance(t, PreTrainedTokenizerFast) for t in tokenizers):
        raise TypeError("Fast tokenizers are required to fingerprint their complete configuration")
    embedding, reranker = tokenizers
    if budget.embedding_limit > embedding.model_max_length:
        raise ValueError("Embedding budget exceeds tokenizer model limit")
    if budget.reranker_limit > reranker.model_max_length:
        raise ValueError("Reranker budget exceeds tokenizer model limit")
    if (
        budget.query_reserve + reranker.num_special_tokens_to_add(pair=True)
        >= budget.reranker_limit
    ):
        raise ValueError("Query reserve and pair special tokens leave no passage capacity")
    fingerprints = [sha256(t.backend_tokenizer.to_str().encode()).hexdigest() for t in tokenizers]
    reports: list[TableChunkReport] = []
    seen: set[str] = set()
    for path in paths:
        content = path.read_bytes()
        inspection = TableInspectionReport.model_validate_json(content)
        paper_id = inspection.case.paper_id
        if paper_id in seen:
            raise ValueError(f"Duplicate inspection for {paper_id}")
        if (output_dir / f"{paper_id}.json").exists():
            raise FileExistsError(
                f"Budgeted evidence already exists for {paper_id} in {output_dir}"
            )
        seen.add(paper_id)
        title = Path(inspection.source_filename).stem
        tables: list[BudgetedTable] = []
        position = 0
        for native in inspection.tables:
            table = budget_table_rows(
                render_table_evidence(native),
                paper_id,
                title,
                budget,
                embedding,
                reranker,
                position,
            )
            position += len(table.rows)
            if table.issues:
                logger.warning("%s %s: %s", paper_id, table.reference, table.issues)
            tables.append(table)
        reports.append(
            TableChunkReport(
                paper_id=paper_id,
                inspection_filename=path.name,
                inspection_sha256=sha256(content).hexdigest(),
                source_sha256=inspection.source_sha256,
                filename_title=title,
                embedding_model=embedding_model,
                reranker_model=reranker_model,
                embedding_tokenizer_sha256=fingerprints[0],
                reranker_tokenizer_sha256=fingerprints[1],
                transformers_version=version("transformers"),
                tokenizers_version=version("tokenizers"),
                budget=budget,
                tables=tables,
            )
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    for report in reports:
        with (output_dir / f"{report.paper_id}.json").open("x", encoding="utf-8") as destination:
            destination.write(report.model_dump_json(indent=2) + "\n")
    return reports

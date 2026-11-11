"""Judge a benchmark extraction run against the hand-extracted reference workbook."""

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from google.adk.runners import Runner
from google.genai.errors import APIError

from steel_slag_slm.agents.extraction_judge import adjudicate_paper, format_claims, judge_table
from steel_slag_slm.agents.runner import StructuredAnswer, gather_bounded
from steel_slag_slm.config import ExtractionJudgeSettings
from steel_slag_slm.data.extraction_tables import SHEET_TABLES, SheetTable, data_columns
from steel_slag_slm.data.reference_workbook import SheetRows
from steel_slag_slm.evaluation.extraction_metrics import JUDGED_TABLES, build_cells, count_filled
from steel_slag_slm.exceptions import AgentOutputError
from steel_slag_slm.llm.pricing import gemini_cost_usd
from steel_slag_slm.llm.prompts import PromptTemplate
from steel_slag_slm.schemas import (
    Adjudication,
    ExtractionRun,
    JudgedCell,
    JudgedTable,
    TableJudgement,
    TokenUsage,
)

logger = logging.getLogger(__name__)

# Failures that cost one sheet or paper, not the run; that part is reported as unjudged
_PER_CALL_ERRORS = (AgentOutputError, APIError, TimeoutError)
_JUDGED = {sheet_table.table for sheet_table in JUDGED_TABLES}
_SHEET_NAMES = {sheet_table.table: sheet_table.sheet for sheet_table in SHEET_TABLES}
# Outcomes the adjudicator re-checks: values the reference lacks or contradicts
_DISPUTED = ("extra", "mismatch")


@dataclass
class Judging:
    """A run's judged cells and sheets, what failed, and the judge's token usage."""

    cells: list[JudgedCell] = field(default_factory=list)
    tables: list[JudgedTable] = field(default_factory=list)
    unjudged: list[str] = field(default_factory=list)  # "paper sheet" that failed
    unadjudicated: list[str] = field(default_factory=list)  # papers whose check failed
    usage: list[TokenUsage] = field(default_factory=list)

    def cost_usd(self, settings: ExtractionJudgeSettings) -> float:
        return sum(gemini_cost_usd(usage, settings.prices) for usage in self.usage)


@dataclass(frozen=True, slots=True)
class _SheetJob:
    paper_id: str
    sheet_table: SheetTable
    reference_rows: list[dict[str, str | None]]
    extracted_rows: list[dict[str, str | None]]


def _needs_judge(job: _SheetJob) -> bool:
    """Only a judged sheet with rows on both sides needs pairing; the rest is decided in code."""
    return job.sheet_table.table in _JUDGED and bool(job.reference_rows and job.extracted_rows)


def _record(judging: Judging, job: _SheetJob, judgement: TableJudgement | None) -> None:
    """Score one paper's sheet and add its cells and counts to the run's judging."""
    columns = data_columns(job.sheet_table)
    matched: int | None = None
    if job.sheet_table.table in _JUDGED:
        cells, matched = build_cells(
            job.paper_id, job.sheet_table, job.reference_rows, job.extracted_rows, judgement
        )
        judging.cells += cells
    judging.tables.append(
        JudgedTable(
            source_id=job.paper_id,
            table=job.sheet_table.table,
            reference_rows=len(job.reference_rows),
            extracted_rows=len(job.extracted_rows),
            reference_filled=count_filled(job.reference_rows, columns),
            extracted_filled=count_filled(job.extracted_rows, columns),
            matched_rows=matched,
        )
    )


async def judge_run(
    settings: ExtractionJudgeSettings,
    runner: Runner,
    prompt: PromptTemplate,
    run: ExtractionRun,
    reference: SheetRows,
    extracted: SheetRows,
) -> Judging:
    """Pair and score every sheet of every paper in the run.

    Only sheets with rows on both sides go to the judge; the rest are all missed or all
    extra, and the provenance sheet only gets counted.
    """
    judging = Judging()
    jobs = [
        _SheetJob(
            paper_id,
            sheet_table,
            reference.get((paper_id, sheet_table.table), []),
            extracted.get((paper_id, sheet_table.table), []),
        )
        for paper_id in run.paper_ids
        for sheet_table in SHEET_TABLES
    ]
    for paper_id in run.paper_ids:
        if not any(job.extracted_rows for job in jobs if job.paper_id == paper_id):
            logger.warning("%s has no extracted rows in this run; all its values count as missed",
                           paper_id)  # fmt: skip
    to_judge = [job for job in jobs if _needs_judge(job)]
    for job in jobs:
        if not _needs_judge(job):
            _record(judging, job, None)

    async def judge(job: _SheetJob) -> StructuredAnswer[TableJudgement]:
        return await asyncio.wait_for(
            judge_table(runner, prompt, job.sheet_table, job.reference_rows, job.extracted_rows),
            settings.call_timeout_s,
        )

    logger.info("Judging %d sheets with %s", len(to_judge), settings.model)
    results = await gather_bounded(to_judge, judge, settings.max_concurrency)
    for job, result in zip(to_judge, results, strict=True):
        if isinstance(result, _PER_CALL_ERRORS):
            logger.warning("Judging %s %s failed: %r", job.paper_id, job.sheet_table.table, result)
            judging.unjudged.append(f"{job.paper_id} {job.sheet_table.table}")
            continue
        if isinstance(result, Exception):
            raise result
        judging.usage.append(result.usage)
        _record(judging, job, result.result)
    return judging


async def adjudicate_run(
    settings: ExtractionJudgeSettings,
    runner: Runner,
    prompt: PromptTemplate,
    judging: Judging,
    extracted: SheetRows,
    load_markdown: Callable[[str], str | None],
) -> None:
    """Check each paper's extra and mismatched values against its text, in place.

    load_markdown returns a paper's parsed Markdown, or None if it can't be read.
    """
    claims_by_paper: dict[str, list[tuple[JudgedCell, dict[str, str | None]]]] = {}
    for cell in judging.cells:
        if cell.outcome in _DISPUTED and cell.extracted_row is not None:
            row = extracted[(cell.source_id, cell.table)][cell.extracted_row - 1]
            claims_by_paper.setdefault(cell.source_id, []).append((cell, row))
    # Drive downloads one at a time; its client isn't thread-safe
    papers = []
    for paper_id in claims_by_paper:
        markdown = load_markdown(paper_id)
        if markdown is None:
            judging.unadjudicated.append(paper_id)
        else:
            papers.append((paper_id, markdown))

    async def adjudicate(paper: tuple[str, str]) -> StructuredAnswer[Adjudication]:
        paper_id, markdown = paper
        claims = format_claims(claims_by_paper[paper_id], _SHEET_NAMES)
        return await asyncio.wait_for(
            adjudicate_paper(runner, prompt, paper_id, markdown, claims), settings.call_timeout_s
        )

    logger.info("Adjudicating %d disputed values in %d papers",
                sum(len(claims) for claims in claims_by_paper.values()), len(papers))  # fmt: skip
    results = await gather_bounded(papers, adjudicate, settings.max_concurrency)
    for (paper_id, _), result in zip(papers, results, strict=True):
        if isinstance(result, _PER_CALL_ERRORS):
            logger.warning("Adjudicating %s failed: %r", paper_id, result)
            judging.unadjudicated.append(paper_id)
            continue
        if isinstance(result, Exception):
            raise result
        judging.usage.append(result.usage)
        claims = claims_by_paper[paper_id]
        for verdict in result.result.verdicts:
            if 1 <= verdict.claim_id <= len(claims):
                cell = claims[verdict.claim_id - 1][0]
                cell.adjudication = verdict.verdict
                cell.adjudication_reason = verdict.reason


def unjudged_papers(tables: Sequence[JudgedTable], paper_ids: Sequence[str]) -> list[str]:
    """Papers missing a judged entry for any sheet, so their scores would be incomplete."""
    judged = {(table.source_id, table.table) for table in tables}
    return [
        paper_id
        for paper_id in paper_ids
        if any((paper_id, sheet_table.table) not in judged for sheet_table in SHEET_TABLES)
    ]

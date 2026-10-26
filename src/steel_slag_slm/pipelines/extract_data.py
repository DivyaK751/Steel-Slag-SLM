"""Extract workbook data values from parsed papers in Drive into the BigQuery sheet tables."""

import asyncio
import logging
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from google.adk.runners import Runner
from google.cloud import bigquery
from google.genai.errors import APIError
from googleapiclient.errors import HttpError

from steel_slag_slm.agents.data_extractor import extract_paper_data
from steel_slag_slm.agents.runner import StructuredAnswer, gather_bounded
from steel_slag_slm.config import DataExtractionSettings, PdfParsingSettings
from steel_slag_slm.data import drive_storage, parsed_index
from steel_slag_slm.data.extraction_rows import build_rows
from steel_slag_slm.data.extraction_tables import list_extracted_ids, replace_paper_rows
from steel_slag_slm.data.paper_registry import read_citations
from steel_slag_slm.data.parsed_document import parse_parsed_json
from steel_slag_slm.exceptions import AgentOutputError
from steel_slag_slm.llm.pricing import bigquery_query_cost_usd, gemini_cost_usd
from steel_slag_slm.llm.prompts import PromptTemplate
from steel_slag_slm.schemas import PaperCitation, PaperExtraction, ParsedPaper

logger = logging.getLogger(__name__)

# Failures that cost one paper, not the run; its rows stay as they were for the next run
_PER_PAPER_ERRORS = (AgentOutputError, APIError, TimeoutError)


def select_papers(
    parsed_ids: Sequence[str],
    requested_ids: Sequence[str] | None,
    extracted_ids: set[str],
    max_papers: int | None,
) -> list[str]:
    """Pick the papers to extract, in order.

    Requested papers are (re-)extracted even if they already have rows; otherwise every
    parsed paper without rows is picked. max_papers caps either list.
    """
    if requested_ids:
        pending = [paper_id for paper_id in requested_ids if paper_id in set(parsed_ids)]
    else:
        pending = [paper_id for paper_id in parsed_ids if paper_id not in extracted_ids]
    return pending if max_papers is None else pending[:max_papers]


def _load_paper(drive_service: Any, paper_id: str, json_file_id: str) -> ParsedPaper | None:
    """Download and read a paper's parsed.json, or None if it can't be read this run."""
    try:
        return parse_parsed_json(drive_storage.download_file(drive_service, json_file_id))
    except (HttpError, TimeoutError, ConnectionError, ValueError) as error:
        logger.warning("Can't read parsed.json of %s, will retry next run: %s", paper_id, error)
        return None


def _store_paper(
    bq_client: bigquery.Client,
    settings: DataExtractionSettings,
    paper: ParsedPaper,
    answer: StructuredAnswer[PaperExtraction],
    citation: PaperCitation | None,
    outcomes: Counter[str],
) -> float:
    """Check one paper's answer, replace its rows in BigQuery, count and log it.

    Returns:
        What the paper cost in US$: Gemini tokens at list price plus the BigQuery delete.
    """
    extraction, usage = answer.result, answer.usage
    rows = build_rows(
        extraction,
        paper.openalex_id,
        citation,
        settings.model,
        datetime.now(UTC),
        paper.content_blocks,
    )
    for reason in rows.rejected:
        logger.warning("%s: dropped %s", paper.openalex_id, reason)
    bytes_billed = replace_paper_rows(
        bq_client, f"{bq_client.project}.{settings.dataset}", paper.openalex_id, rows.rows_by_table
    )
    gemini_usd = gemini_cost_usd(usage, settings.prices)
    bigquery_usd = bigquery_query_cost_usd(bytes_billed, settings.bigquery_usd_per_tib)
    stored = len(extraction.facts) - len(rows.rejected)
    outcomes["extracted"] += 1
    outcomes["facts_stored"] += stored
    outcomes["facts_rejected"] += len(rows.rejected)
    outcomes["confidence_lowered"] += rows.confidence_lowered
    outcomes["facts_without_page"] += rows.without_page
    outcomes["input_tokens"] += usage.input_tokens
    outcomes["output_tokens"] += usage.output_tokens
    logger.info(
        "%s: %d materials, %d facts stored, %d rejected | %d input + %d output tokens"
        " | cost $%.4f (Gemini $%.4f, BigQuery $%.6f)",
        paper.openalex_id, len(extraction.materials), stored, len(rows.rejected),
        usage.input_tokens, usage.output_tokens,
        gemini_usd + bigquery_usd, gemini_usd, bigquery_usd,
    )  # fmt: skip
    return gemini_usd + bigquery_usd


async def run_data_extraction(
    settings: DataExtractionSettings,
    parsing: PdfParsingSettings,
    bq_client: bigquery.Client,
    drive_service: Any,
    parsed_folder_id: str,
    runner: Runner,
    prompt: PromptTemplate,
    paper_ids: Sequence[str] | None = None,
    max_papers: int | None = None,
) -> Counter[str]:
    """Extract each selected paper and replace its rows in the sheet tables.

    Each paper's cost is logged as it finishes, and the run's total at the end.

    Returns:
        Counts of papers extracted, failed and not parsed, facts stored, rejected, given a
        lower confidence or not placed on a page, and Gemini input and output tokens.
    """
    index = parsed_index.load_index(drive_service, parsed_folder_id, parsing.index_file_name)
    parsed = {paper_id: e for paper_id, e in index.items() if e.get("status") != "skipped"}
    outcomes: Counter[str] = Counter()
    if paper_ids:
        outcomes["not_parsed"] = len(set(paper_ids) - parsed.keys())
    extracted = (
        set()
        if paper_ids
        else list_extracted_ids(bq_client, f"{bq_client.project}.{settings.dataset}")
    )
    pending = select_papers(list(parsed), paper_ids, extracted, max_papers)
    citations = read_citations(bq_client, settings.papers_table, pending)
    logger.info(
        "Extracting %d papers with %s, prompt %s v%d",
        len(pending), settings.model, prompt.name, prompt.version,
    )  # fmt: skip

    async def extract(paper: ParsedPaper) -> StructuredAnswer[PaperExtraction]:
        citation = citations.get(paper.openalex_id)
        title = citation.title if citation else None
        return await asyncio.wait_for(
            extract_paper_data(runner, prompt, paper, title), settings.call_timeout_s
        )

    total_cost_usd = 0.0
    # Batches keep Drive downloads (one at a time: its client isn't thread-safe) and
    # BigQuery writes between rounds of concurrent Gemini calls
    for start in range(0, len(pending), settings.max_concurrency):
        batch = pending[start : start + settings.max_concurrency]
        loaded = [_load_paper(drive_service, p, str(parsed[p]["json_file_id"])) for p in batch]
        papers = [paper for paper in loaded if paper is not None]
        outcomes["failed"] += len(loaded) - len(papers)

        results = await gather_bounded(papers, extract, settings.max_concurrency)
        for paper, result in zip(papers, results, strict=True):
            if isinstance(result, _PER_PAPER_ERRORS):
                logger.warning("Extracting %s failed: %r", paper.openalex_id, result)
                outcomes["failed"] += 1
                continue
            if isinstance(result, Exception):
                raise result
            total_cost_usd += _store_paper(
                bq_client, settings, paper, result, citations.get(paper.openalex_id), outcomes
            )
    logger.info("Total cost of this run: $%.4f", total_cost_usd)
    return outcomes

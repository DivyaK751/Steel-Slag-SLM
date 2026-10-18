"""Fill missing abstracts in BigQuery from the first pages of each paper's PDF in Drive."""

import asyncio
import logging
from collections import Counter
from typing import Any

from google.adk.runners import Runner
from google.cloud import bigquery
from google.genai.errors import APIError
from googleapiclient.errors import HttpError

from steel_slag_slm.agents.abstract_extractor import (
    AbstractOutcome,
    extract_abstract,
    judge_abstract,
)
from steel_slag_slm.agents.runner import gather_bounded
from steel_slag_slm.config import AbstractExtractionSettings
from steel_slag_slm.data import drive_storage
from steel_slag_slm.data.bigquery_store import merge_updates, query_rows
from steel_slag_slm.data.pdf_pages import first_pages, page_text
from steel_slag_slm.exceptions import AgentOutputError
from steel_slag_slm.llm.prompts import PromptTemplate
from steel_slag_slm.schemas import PaperForAbstract

logger = logging.getLogger(__name__)

# Columns this pipeline fills; everything else in the table belongs to other jobs
ABSTRACT_COLUMN_TYPES = {"openalex_id": "STRING", "abstract": "STRING", "abstract_source": "STRING"}
# Failures that cost one paper, not the run; the paper stays untouched for the next run.
# TimeoutError: a Gemini call that hangs would otherwise hold up its whole batch
_PER_PAPER_ERRORS = (AgentOutputError, APIError, TimeoutError)

_MISSING_SQL = """
SELECT openalex_id, title, drive_file_id
FROM `{table}`
WHERE abstract IS NULL
  AND abstract_source IS NULL
  AND drive_file_id IS NOT NULL
  AND openalex_id NOT IN UNNEST(@attempted_ids)
ORDER BY added_at
LIMIT @limit
"""


def fetch_missing(
    client: bigquery.Client, table_id: str, limit: int, attempted_ids: set[str]
) -> list[PaperForAbstract]:
    """Return up to limit papers with no abstract that haven't been tried yet, oldest first."""
    rows = query_rows(
        client,
        _MISSING_SQL.format(table=table_id),
        {"limit": limit, "attempted_ids": sorted(attempted_ids)},
    )
    return [
        PaperForAbstract(row["openalex_id"], row["title"] or "", row["drive_file_id"])
        for row in rows
    ]


def _load_pages(
    drive_service: Any, paper: PaperForAbstract, max_pages: int
) -> tuple[bytes, str] | None:
    """Download the paper's PDF and return its first pages and their text, or None if unusable."""
    try:
        pdf = drive_storage.download_pdf(drive_service, paper.drive_file_id)
        pages = first_pages(pdf, max_pages)
        return pages, page_text(pages)
    # A missing file, a network hiccup or a corrupt PDF: try again on the next run
    except (HttpError, TimeoutError, ConnectionError, RuntimeError) as error:
        logger.warning(
            "Can't read the PDF of %s, will retry next run: %s", paper.openalex_id, error
        )
        return None


async def run_fill_abstracts(
    settings: AbstractExtractionSettings,
    bq_client: bigquery.Client,
    drive_service: Any,
    runner: Runner,
    prompt: PromptTemplate,
    max_papers: int | None,
    dry_run: bool = False,
) -> Counter[str]:
    """Extract abstracts batch by batch until none are missing or max_papers have been tried.

    Returns:
        Counts per abstract_source (pdf, pdf_unverified, pdf_mismatch, not_found) plus failed
        and written.
    """
    outcomes: Counter[str] = Counter()
    # Failed (and, in a dry run, all) papers stay NULL, so skip them or we'd fetch them forever
    attempted_ids: set[str] = set()
    logger.info(
        "Extracting abstracts with %s, prompt %s v%d", settings.model, prompt.name, prompt.version
    )

    while max_papers is None or len(attempted_ids) < max_papers:
        limit = settings.batch_size
        if max_papers is not None:
            limit = min(limit, max_papers - len(attempted_ids))
        papers = fetch_missing(bq_client, settings.papers_table, limit, attempted_ids)
        if not papers:
            break
        attempted_ids.update(paper.openalex_id for paper in papers)

        # Drive's client isn't safe to share across threads, so downloads run one at a time
        loaded = [
            (paper, _load_pages(drive_service, paper, settings.max_pages)) for paper in papers
        ]
        readable = [(paper, pages) for paper, pages in loaded if pages is not None]
        outcomes["failed"] += len(loaded) - len(readable)

        async def extract(item: tuple[PaperForAbstract, tuple[bytes, str]]) -> AbstractOutcome:
            paper, (pages_pdf, pages_text) = item
            result = await asyncio.wait_for(
                extract_abstract(runner, prompt, paper, pages_pdf), settings.call_timeout_s
            )
            outcome = judge_abstract(result, pages_text)
            # Logged as each paper finishes, so a long batch still shows progress
            match = "" if outcome.match_share is None else f" (match {outcome.match_share:.0%})"
            logger.info(
                "%s %s%s: %s",
                paper.openalex_id,
                outcome.source,
                match,
                (outcome.abstract or "")[:120],
            )
            return outcome

        results = await gather_bounded(readable, extract, settings.max_concurrency)
        rows = []
        for (paper, _), result in zip(readable, results, strict=True):
            if isinstance(result, _PER_PAPER_ERRORS):
                logger.warning(
                    "Extracting %s failed, will retry next run: %r", paper.openalex_id, result
                )
                outcomes["failed"] += 1
                continue
            if isinstance(result, Exception):
                raise result
            outcomes[result.source] += 1
            rows.append(
                {
                    "openalex_id": paper.openalex_id,
                    "abstract": result.abstract,
                    "abstract_source": result.source,
                }
            )

        if not dry_run:
            outcomes["written"] += merge_updates(
                bq_client, settings.papers_table, "openalex_id", rows, ABSTRACT_COLUMN_TYPES
            )
    return outcomes

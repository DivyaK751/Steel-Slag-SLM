"""Screen unscreened papers in BigQuery with the relevance agent and write the decisions back."""

import logging
from collections import Counter
from typing import Any

from google.adk.runners import Runner
from google.cloud import bigquery
from google.genai.errors import APIError

from steel_slag_slm.agents.relevance_screener import paper_from_row, screen_paper
from steel_slag_slm.agents.runner import gather_bounded
from steel_slag_slm.config import ScreeningSettings
from steel_slag_slm.data.bigquery_store import merge_updates, query_rows
from steel_slag_slm.exceptions import AgentOutputError
from steel_slag_slm.llm.prompts import PromptTemplate
from steel_slag_slm.schemas import PaperForScreening, RelevanceDecision

logger = logging.getLogger(__name__)

# Columns this pipeline fills; everything else in the table belongs to the ingestion job
DECISION_COLUMN_TYPES = {
    "openalex_id": "STRING",
    "relevant": "STRING",
    "relevance_score": "FLOAT64",
    "relevance_confidence": "STRING",
    "relevance_reason": "STRING",
    "supporting_excerpt": "STRING",
}
# Failures that cost one paper, not the run; the paper stays unscreened for the next run
_PER_PAPER_ERRORS = (AgentOutputError, APIError)

_UNSCREENED_SQL = """
SELECT openalex_id, title, abstract, year, keywords
FROM `{table}`
WHERE relevant IS NULL
  AND openalex_id NOT IN UNNEST(@attempted_ids)
ORDER BY added_at
LIMIT @limit
"""


def fetch_unscreened(
    client: bigquery.Client, table_id: str, limit: int, attempted_ids: set[str]
) -> list[PaperForScreening]:
    """Return up to limit papers with no decision yet, oldest first, skipping attempted_ids."""
    rows = query_rows(
        client,
        _UNSCREENED_SQL.format(table=table_id),
        {"limit": limit, "attempted_ids": sorted(attempted_ids)},
    )
    return [paper_from_row(row) for row in rows]


def decision_row(paper: PaperForScreening, decision: RelevanceDecision) -> dict[str, Any]:
    """The BigQuery update for one paper, keyed by its ID rather than the model's echo of it."""
    return {
        **decision.model_dump(include=set(DECISION_COLUMN_TYPES)),
        "openalex_id": paper.openalex_id,
    }


async def run_screening(
    settings: ScreeningSettings,
    bq_client: bigquery.Client,
    runner: Runner,
    prompt: PromptTemplate,
    max_papers: int | None,
    dry_run: bool = False,
) -> Counter[str]:
    """Screen papers batch by batch until none are left or max_papers have been tried.

    Returns:
        Counts of decisions and problems: yes, no, unclear, failed, no_excerpt, written.
    """
    outcomes: Counter[str] = Counter()
    # Failed (and, in a dry run, all) papers stay NULL, so skip them or we'd fetch them forever
    attempted_ids: set[str] = set()
    logger.info("Screening with %s, prompt %s v%d", settings.model, prompt.name, prompt.version)

    while max_papers is None or len(attempted_ids) < max_papers:
        limit = settings.batch_size
        if max_papers is not None:
            limit = min(limit, max_papers - len(attempted_ids))
        papers = fetch_unscreened(bq_client, settings.papers_table, limit, attempted_ids)
        if not papers:
            break
        attempted_ids.update(paper.openalex_id for paper in papers)

        results = await gather_bounded(
            papers, lambda paper: screen_paper(runner, prompt, paper), settings.max_concurrency
        )
        rows = []
        for paper, result in zip(papers, results, strict=True):
            if isinstance(result, _PER_PAPER_ERRORS):
                logger.warning(
                    "Screening %s failed, will retry next run: %s", paper.openalex_id, result
                )
                outcomes["failed"] += 1
                continue
            if isinstance(result, Exception):
                raise result
            outcomes[result.relevant.lower()] += 1
            if result.supporting_excerpt is None:
                outcomes["no_excerpt"] += 1
            logger.info(
                "%s %s (%.2f): %s",
                paper.openalex_id,
                result.relevant,
                result.relevance_score,
                result.relevance_reason,
            )
            rows.append(decision_row(paper, result))

        if not dry_run:
            outcomes["written"] += merge_updates(
                bq_client, settings.papers_table, "openalex_id", rows, DECISION_COLUMN_TYPES
            )
    return outcomes

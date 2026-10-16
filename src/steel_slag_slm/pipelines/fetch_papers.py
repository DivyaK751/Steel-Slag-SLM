"""Fetch new relevant papers from OpenAlex into the shared Drive folder until the budget ends."""

import logging
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from google.cloud import bigquery

from steel_slag_slm.config import Settings
from steel_slag_slm.data import drive_storage, paper_registry
from steel_slag_slm.data.openalex import OpenAlexClient
from steel_slag_slm.data.relevance import is_relevant
from steel_slag_slm.exceptions import BudgetExhaustedError, OpenAlexRequestError
from steel_slag_slm.schemas import Paper

logger = logging.getLogger(__name__)


def _iter_unique_papers(client: OpenAlexClient, queries: list[str]) -> Iterator[Paper]:
    """Yield papers across all queries, skipping ones an earlier query already returned."""
    seen_ids: set[str] = set()
    for query in queries:
        logger.info("Searching: %s", query)
        try:
            for paper in client.iter_papers(query):
                if paper.openalex_id not in seen_ids:
                    seen_ids.add(paper.openalex_id)
                    yield paper
        except OpenAlexRequestError as error:
            logger.warning("Search for %s failed, moving to the next query: %s", query, error)


def _save_registry_rows(
    bigquery_client: bigquery.Client, settings: Settings, rows: list[dict[str, Any]]
) -> None:
    if not rows:
        return
    table_id = paper_registry.ensure_papers_table(bigquery_client, settings.registry)
    paper_registry.add_rows(bigquery_client, table_id, rows)
    logger.info("Added %d papers to the BigQuery registry", len(rows))


def run_fetch(
    settings: Settings,
    drive_service: Any,
    client: OpenAlexClient,
    max_new_papers: int | None,
    bigquery_client: bigquery.Client | None = None,
) -> Counter[str]:
    """Add new relevant papers to Drive, stopping at max_new_papers or when the budget runs out.

    With a BigQuery client, each uploaded paper also gets a registry row (abstract, DOI,
    keywords, language, ...), so nothing has to look it up again later.

    Returns:
        Counts of what happened to each paper: added_free, added_openalex, irrelevant,
        already_known, no_pdf_before, no_pdf, download_error.
    """
    folder_id = settings.drive_folder_id
    no_pdf_list_name = settings.drive.no_pdf_list_name
    known_ids = drive_storage.list_known_paper_ids(
        drive_service, folder_id, settings.drive.rejected_subfolder_name
    )
    no_pdf_ids = drive_storage.read_id_list(drive_service, folder_id, no_pdf_list_name)
    logger.info(
        "%d papers already in Drive, %d known to have no PDF", len(known_ids), len(no_pdf_ids)
    )
    outcomes: Counter[str] = Counter()
    registry_rows: list[dict[str, Any]] = []

    try:
        for paper in _iter_unique_papers(client, settings.fetch.queries):
            if paper.openalex_id in known_ids:
                outcomes["already_known"] += 1
                continue
            if paper.openalex_id in no_pdf_ids:
                outcomes["no_pdf_before"] += 1
                continue
            if not is_relevant(paper, settings.relevance):
                outcomes["irrelevant"] += 1
                continue

            # Re-check right before paying: another member's run may have added it since we listed
            if drive_storage.has_paper(drive_service, folder_id, paper.openalex_id):
                known_ids.add(paper.openalex_id)
                outcomes["already_known"] += 1
                continue
            try:
                download = client.download_pdf(paper)
            except OpenAlexRequestError as error:
                # A network hiccup, not a missing PDF: stay off the no-PDF list so tomorrow retries
                logger.warning("Download failed for %s, skipping: %s", paper.openalex_id, error)
                outcomes["download_error"] += 1
                continue
            if download is None:
                no_pdf_ids.add(paper.openalex_id)
                outcomes["no_pdf"] += 1
                continue

            pdf, pdf_source = download
            file_id = drive_storage.upload_pdf(drive_service, folder_id, paper, pdf, pdf_source)
            registry_rows.append(paper_registry.build_row(paper, file_id, datetime.now(UTC)))
            known_ids.add(paper.openalex_id)
            outcomes[f"added_{pdf_source}"] += 1
            logger.info("Added (%s) %s: %s", pdf_source, paper.openalex_id, paper.title[:90])

            added = outcomes["added_free"] + outcomes["added_openalex"]
            if max_new_papers is not None and added >= max_new_papers:
                break
    except BudgetExhaustedError as error:
        logger.info("Stopping: %s", error)
    finally:
        # Saved even if the run crashes, so the next run doesn't retry these
        if outcomes["no_pdf"]:
            drive_storage.write_id_list(drive_service, folder_id, no_pdf_list_name, no_pdf_ids)
        # One load job per run; if it fails, the nightly registry sync adds these rows instead
        if bigquery_client is not None:
            _save_registry_rows(bigquery_client, settings, registry_rows)

    return outcomes

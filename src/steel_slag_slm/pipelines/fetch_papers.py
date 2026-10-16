"""Download queued papers into Drive, searching deeper for more whenever the queue runs dry."""

import logging
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from google.cloud import bigquery

from steel_slag_slm.config import Settings
from steel_slag_slm.data import drive_storage, paper_registry
from steel_slag_slm.data.openalex import MAX_IDS_PER_LOOKUP, OpenAlexClient
from steel_slag_slm.data.search_progress import format_progress, parse_progress
from steel_slag_slm.exceptions import BudgetExhaustedError, OpenAlexRequestError
from steel_slag_slm.pipelines.discover_papers import discover_papers
from steel_slag_slm.schemas import Paper

logger = logging.getLogger(__name__)


def _save_registry_rows(
    bigquery_client: bigquery.Client, settings: Settings, rows: list[dict[str, Any]]
) -> None:
    if not rows:
        return
    table_id = paper_registry.ensure_papers_table(bigquery_client, settings.registry)
    paper_registry.add_rows(bigquery_client, table_id, rows)
    logger.info("Added %d papers to the BigQuery registry", len(rows))


def _count_added(outcomes: Counter[str]) -> int:
    return outcomes["added_free"] + outcomes["added_openalex"]


def _fetch_paper(
    settings: Settings, drive_service: Any, client: OpenAlexClient, paper: Paper
) -> tuple[str, str | None]:
    """Download one paper into Drive, returning its outcome and, if uploaded, its file ID."""
    folder_id = settings.drive_folder_id
    # Re-check right before paying: someone may have added it by hand since it was queued
    if drive_storage.has_paper(drive_service, folder_id, paper.openalex_id):
        return "already_known", None
    try:
        download = client.download_pdf(paper)
    except OpenAlexRequestError as error:
        logger.warning("Download failed for %s, keeping it queued: %s", paper.openalex_id, error)
        return "download_error", None
    if download is None:
        return "no_pdf", None
    pdf, pdf_source = download
    file_id = drive_storage.upload_pdf(drive_service, folder_id, paper, pdf, pdf_source)
    logger.info("Added (%s) %s: %s", pdf_source, paper.openalex_id, paper.title[:90])
    return f"added_{pdf_source}", file_id


def run_fetch(
    settings: Settings,
    drive_service: Any,
    client: OpenAlexClient,
    max_new_papers: int | None,
    bigquery_client: bigquery.Client | None = None,
) -> Counter[str]:
    """Download queued papers into Drive until max_new_papers or the budget runs out.

    The queue is a text file of OpenAlex IDs in Drive. Whenever it runs dry, every search
    reads its next pages to refill it; once every search has been read to its end with
    nothing new, the run stops. Searches newly added to the config are read first, even
    if the queue isn't empty. With a BigQuery client, each uploaded paper also gets a
    registry row.

    Returns:
        Counts: queued, added_free, added_openalex, already_known, not_found, no_pdf,
        download_error.
    """
    folder_id = settings.drive_folder_id
    names = settings.drive
    known_ids = drive_storage.list_known_paper_ids(
        drive_service, folder_id, names.rejected_subfolder_name
    )
    no_pdf_ids = drive_storage.read_id_list(drive_service, folder_id, names.no_pdf_list_name)
    queue = drive_storage.read_id_list(drive_service, folder_id, names.queue_list_name)
    queries = settings.fetch.queries
    saved_progress = parse_progress(
        drive_storage.read_text_file(drive_service, folder_id, names.search_progress_name)
    )
    # Searches removed from the config are dropped; ones added since the last run start now
    progress = {query: page for query, page in saved_progress.items() if query in queries}
    new_queries = [query for query in queries if query not in progress]
    logger.info(
        "%d papers in Drive, %d with no PDF, %d queued", len(known_ids), len(no_pdf_ids), len(queue)
    )
    outcomes: Counter[str] = Counter()
    registry_rows: list[dict[str, Any]] = []
    # Papers that failed for a temporary reason stay queued for tomorrow, not retried today
    retry_tomorrow: set[str] = set()
    finished_searches: set[str] = set()

    try:
        if new_queries:
            logger.info("Reading %d searches added since the last run", len(new_queries))
            queued_before = len(queue)
            discover_papers(settings, client, new_queries, progress, known_ids | no_pdf_ids, queue)
            outcomes["queued"] += len(queue) - queued_before

        while max_new_papers is None or _count_added(outcomes) < max_new_papers:
            pending = sorted(queue - retry_tomorrow)
            if not pending:
                if finished_searches >= set(queries):
                    logger.info("Every search was read to its end with nothing new; stopping")
                    break
                queued_before = len(queue)
                finished_searches |= discover_papers(
                    settings, client, queries, progress, known_ids | no_pdf_ids, queue
                )
                outcomes["queued"] += len(queue) - queued_before
                continue

            batch = pending[:MAX_IDS_PER_LOOKUP]
            papers = {paper.openalex_id: paper for paper in client.iter_papers_by_id(batch)}
            for paper_id in batch:
                if max_new_papers is not None and _count_added(outcomes) >= max_new_papers:
                    break
                paper = papers.get(paper_id)
                outcome, file_id = (
                    ("not_found", None)
                    if paper is None
                    else _fetch_paper(settings, drive_service, client, paper)
                )
                outcomes[outcome] += 1
                if outcome == "download_error":
                    retry_tomorrow.add(paper_id)
                    continue
                queue.discard(paper_id)
                if outcome == "no_pdf":
                    no_pdf_ids.add(paper_id)
                if paper is not None and file_id is not None:
                    known_ids.add(paper_id)
                    registry_rows.append(
                        paper_registry.build_row(paper, file_id, datetime.now(UTC))
                    )
    except BudgetExhaustedError as error:
        logger.info("Stopping: %s", error)
    finally:
        # Saved even if the run crashes, so the next run continues where this one stopped
        drive_storage.write_id_list(drive_service, folder_id, names.queue_list_name, queue)
        drive_storage.write_text_file(
            drive_service, folder_id, names.search_progress_name, format_progress(progress)
        )
        if outcomes["no_pdf"]:
            drive_storage.write_id_list(
                drive_service, folder_id, names.no_pdf_list_name, no_pdf_ids
            )
        # One load job per run; if it fails, the nightly registry sync adds these rows instead
        if bigquery_client is not None:
            _save_registry_rows(bigquery_client, settings, registry_rows)

    return outcomes

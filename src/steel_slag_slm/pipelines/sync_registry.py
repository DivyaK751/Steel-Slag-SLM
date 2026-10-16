"""Give every paper in the Drive folder a row in the BigQuery registry."""

import logging
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from google.cloud import bigquery

from steel_slag_slm.config import Settings
from steel_slag_slm.data import drive_storage, paper_registry
from steel_slag_slm.data.openalex import OpenAlexClient
from steel_slag_slm.exceptions import BudgetExhaustedError

logger = logging.getLogger(__name__)


def run_registry_sync(
    settings: Settings,
    drive_service: Any,
    client: OpenAlexClient,
    bigquery_client: bigquery.Client,
) -> Counter[str]:
    """Add a row for each Drive paper that has none, looking up its details on OpenAlex.

    The first run fills the table for every paper already in Drive; later runs only add
    papers the fetcher missed (e.g. uploaded by hand, or a failed BigQuery write).

    Returns:
        Counts: already_registered, added, not_added (unknown to OpenAlex, or left for the
        next run when the budget runs out).
    """
    table_id = paper_registry.ensure_papers_table(bigquery_client, settings.registry)
    drive_files = drive_storage.list_paper_files(drive_service, settings.drive_folder_id)
    missing_ids = sorted(
        drive_files.keys() - paper_registry.list_registered_ids(bigquery_client, table_id)
    )
    logger.info("%d papers in Drive, %d without a registry row", len(drive_files), len(missing_ids))

    added_at = datetime.now(UTC)
    rows = []
    try:
        for paper in client.iter_papers_by_id(missing_ids):
            # OpenAlex can answer an old ID with a merged work's new ID; skip those
            if paper.openalex_id in drive_files:
                rows.append(
                    paper_registry.build_row(paper, drive_files[paper.openalex_id], added_at)
                )
    except BudgetExhaustedError as error:
        logger.info("Stopping early, the rest are added next run: %s", error)
    finally:
        # Saved even if the lookup stops early, so finished lookups aren't paid for twice
        paper_registry.add_rows(bigquery_client, table_id, rows)

    return Counter(
        already_registered=len(drive_files) - len(missing_ids),
        added=len(rows),
        not_added=len(missing_ids) - len(rows),
    )

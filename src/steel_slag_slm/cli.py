"""Command-line entry points."""

import argparse
import logging
from typing import Any

from google.cloud import bigquery

from steel_slag_slm.config import Settings, load_settings
from steel_slag_slm.data.drive_storage import build_drive_service
from steel_slag_slm.data.openalex import OpenAlexClient
from steel_slag_slm.data.paper_registry import build_bigquery_client
from steel_slag_slm.pipelines.fetch_papers import run_fetch
from steel_slag_slm.pipelines.sync_registry import run_registry_sync

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _build_openalex_client(settings: Settings) -> OpenAlexClient:
    return OpenAlexClient(
        settings.openalex_api_key.get_secret_value(), settings.contact_email, settings.fetch
    )


def _build_drive_service(settings: Settings) -> Any:
    return build_drive_service(
        settings.google_service_account_file,
        settings.google_oauth_client_file,
        settings.google_oauth_token_file,
    )


def _build_registry_client(settings: Settings) -> bigquery.Client | None:
    if not settings.gcp_project:
        return None
    return build_bigquery_client(settings.gcp_project, settings.gcp_credentials_file)


def _print_remaining_budget(client: OpenAlexClient) -> None:
    if client.remaining_budget_usd is not None:
        print(f"Remaining OpenAlex budget today: ${client.remaining_budget_usd:.3f}")


def fetch_papers() -> None:
    """Download new relevant papers into the shared Drive folder."""
    parser = argparse.ArgumentParser(description=fetch_papers.__doc__)
    parser.add_argument(
        "--max-papers",
        type=int,
        default=None,
        help="stop after adding this many papers (default: run until the daily budget is used)",
    )
    args = parser.parse_args()
    _setup_logging()

    settings = load_settings()
    client = _build_openalex_client(settings)
    registry_client = _build_registry_client(settings)
    if registry_client is None:
        logger.warning("GOOGLE_CLOUD_PROJECT is not set; skipping the BigQuery registry")
    outcomes = run_fetch(
        settings, _build_drive_service(settings), client, args.max_papers, registry_client
    )

    print("Summary:", dict(outcomes))
    _print_remaining_budget(client)


def sync_registry() -> None:
    """Add a BigQuery registry row for every paper in Drive that doesn't have one yet."""
    argparse.ArgumentParser(description=sync_registry.__doc__).parse_args()
    _setup_logging()

    settings = load_settings()
    registry_client = _build_registry_client(settings)
    if registry_client is None:
        raise SystemExit("Set GOOGLE_CLOUD_PROJECT in .env to the project that holds BigQuery")
    client = _build_openalex_client(settings)
    outcomes = run_registry_sync(settings, _build_drive_service(settings), client, registry_client)

    print("Summary:", dict(outcomes))
    _print_remaining_budget(client)

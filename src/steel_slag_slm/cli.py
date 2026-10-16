"""Command-line entry points."""

import argparse
import asyncio
import logging
from typing import Any

from google.cloud import bigquery

from steel_slag_slm.agents.relevance_screener import build_screener_agent
from steel_slag_slm.agents.runner import build_runner
from steel_slag_slm.config import (
    Settings,
    load_gcp_settings,
    load_screening_settings,
    load_settings,
)
from steel_slag_slm.data.drive_storage import build_drive_service
from steel_slag_slm.data.openalex import OpenAlexClient
from steel_slag_slm.data.paper_registry import build_bigquery_client
from steel_slag_slm.llm.prompts import load_prompt
from steel_slag_slm.pipelines.fetch_papers import run_fetch
from steel_slag_slm.pipelines.screen_papers import run_screening
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


def screen_papers() -> None:
    """Fill the relevance columns of unscreened papers in BigQuery using Gemini."""
    parser = argparse.ArgumentParser(description=screen_papers.__doc__)
    parser.add_argument(
        "--max-papers",
        type=int,
        default=None,
        help="stop after screening this many papers (default: all unscreened papers)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="log the decisions without writing them to BigQuery",
    )
    args = parser.parse_args()
    _setup_logging()
    # These log every Gemini request at INFO, which buries the per-paper decisions
    for noisy_logger in ("httpx", "google_genai", "google_adk"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    gcp = load_gcp_settings()
    settings = load_screening_settings()
    prompt = load_prompt(settings.prompt_name)
    runner = build_runner(build_screener_agent(settings, prompt))
    bq_client = build_bigquery_client(gcp.project, gcp.credentials_file)
    outcomes = asyncio.run(
        run_screening(settings, bq_client, runner, prompt, args.max_papers, args.dry_run)
    )
    print("Summary:", dict(outcomes))

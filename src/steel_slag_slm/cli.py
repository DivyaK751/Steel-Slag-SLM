"""Command-line entry points."""

import argparse
import logging

from steel_slag_slm.config import load_settings
from steel_slag_slm.data.drive_storage import build_drive_service
from steel_slag_slm.data.openalex import OpenAlexClient
from steel_slag_slm.pipelines.fetch_papers import run_fetch


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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    settings = load_settings()
    client = OpenAlexClient(
        settings.openalex_api_key.get_secret_value(), settings.contact_email, settings.fetch
    )
    drive_service = build_drive_service(
        settings.google_service_account_file,
        settings.google_oauth_client_file,
        settings.google_oauth_token_file,
    )
    outcomes = run_fetch(settings, drive_service, client, args.max_papers)

    print("Summary:", dict(outcomes))
    if client.remaining_budget_usd is not None:
        print(f"Remaining OpenAlex budget today: ${client.remaining_budget_usd:.3f}")

"""Retrieve OpenAlex candidates and save the raw CSV."""

import argparse
import logging

from . import config
from .io_utils import save_csv
from .openalex_client import fetch_all_candidates


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-pages",
        type=int,
        help="Fetch only this many pages and save a preview CSV.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    records = fetch_all_candidates(max_pages=args.max_pages)

    filename = (
        "candidates_raw_preview.csv"
        if args.max_pages is not None
        else "candidates_raw.csv"
    )
    output_path = config.OUTPUT_DIR / filename
    save_csv(records, output_path, fieldnames=config.SELECT_FIELDS)

    logging.info("Saved %d records to %s", len(records), output_path)


if __name__ == "__main__":
    main()
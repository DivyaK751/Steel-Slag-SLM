"""Convert raw OpenAlex records into clean and no-abstract CSVs."""

import logging

from . import config
from .io_utils import dedup_records, load_csv, route_no_abstract, save_csv

LOGGER = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    raw_path = config.OUTPUT_DIR / "candidates_raw.csv"
    raw_records = load_csv(raw_path)

    unique_records = dedup_records(raw_records)
    duplicates_removed = len(raw_records) - len(unique_records)
    with_abstract, without_abstract = route_no_abstract(unique_records)

    save_csv(with_abstract, config.OUTPUT_DIR / "candidates_clean.csv")
    save_csv(without_abstract, config.OUTPUT_DIR / "no_abstract.csv")

    LOGGER.info("Raw records: %d", len(raw_records))
    LOGGER.info("Duplicates removed: %d", duplicates_removed)
    LOGGER.info("Records with abstracts: %d", len(with_abstract))
    LOGGER.info("Records without abstracts: %d", len(without_abstract))


if __name__ == "__main__":
    main()
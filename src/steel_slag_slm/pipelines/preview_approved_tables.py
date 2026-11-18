"""Write a local preview of contextual rows without accessing Drive."""

import argparse
import logging
from pathlib import Path

from steel_slag_slm.data.approved_table_text import enrich_approved_json
from steel_slag_slm.data.parsed_manifest import ParsedManifest

logger = logging.getLogger(__name__)


def main() -> None:
    """Preview one approved manifest and refuse to overwrite any existing file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Downloaded approved parsed.json")
    parser.add_argument("output", type=Path, help="New local JSON path")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        content = enrich_approved_json(args.source.read_bytes())
        manifest = ParsedManifest.model_validate_json(content)
        tables = manifest.contextual_tables or []
        for table in tables:
            for issue in table.issues:
                logger.warning("%s row %s: %s", table.reference, issue.row_index, issue.reason)
        if not any(table.rows for table in tables):
            raise ValueError("No supported table rows found")
        with args.output.open("xb") as file:
            file.write(content)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Preview failed: {error}\n")

    readings = [image.reading for image in manifest.images if image.reading is not None]
    logger.info(
        "%s: %d contextual rows across %d tables; %d figure readings (%d with data rows). Saved %s",
        manifest.openalex_id,
        sum(len(table.rows) for table in tables),
        sum(bool(table.rows) for table in tables),
        len(readings),
        sum(bool(reading.rows) for reading in readings),
        args.output,
    )


if __name__ == "__main__":
    main()

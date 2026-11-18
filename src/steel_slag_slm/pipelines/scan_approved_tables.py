"""Dry-run contextual table enrichment on approved Drive manifests."""

import argparse
import logging
from collections import Counter, defaultdict
from re import fullmatch
from typing import Any

from googleapiclient.errors import HttpError

from steel_slag_slm.config import load_drive_connection_settings
from steel_slag_slm.data import drive_storage
from steel_slag_slm.data.approved_table_text import enrich_approved_json
from steel_slag_slm.data.parsed_manifest import ParsedManifest

logger = logging.getLogger(__name__)
_APPROVED_FOLDER_NAME = "_parsed_approved"


def _scan_paper(service: Any, paper_id: str, folder_id: str) -> str:
    file_ids = drive_storage.list_named_file_ids(service, folder_id, "parsed.json")
    if len(file_ids) != 1:
        raise ValueError(f"Expected one parsed.json, found {len(file_ids)}")
    source = drive_storage.download_file(service, file_ids[0])
    original = ParsedManifest.model_validate_json(source)
    if original.openalex_id != paper_id:
        raise ValueError("Manifest paper ID does not match the folder name")
    if "contextual_tables" in original.model_fields_set:
        logger.info("%s: already has contextual_tables", paper_id)
        return "already_has_field"

    enriched = ParsedManifest.model_validate_json(enrich_approved_json(source))
    tables = enriched.contextual_tables or []
    issues = [issue for table in tables for issue in table.issues]
    row_count = sum(len(table.rows) for table in tables)
    figure_data_count = sum(bool(image.reading and image.reading.rows) for image in enriched.images)
    for table in tables:
        for issue in table.issues:
            logger.warning(
                "%s %s row %s: %s", paper_id, table.reference, issue.row_index, issue.reason
            )
    logger.info(
        "%s: %d candidate rows across %d table blocks, %d issues, %d figures with data rows",
        paper_id,
        row_count,
        len(tables),
        len(issues),
        figure_data_count,
    )
    if not tables and original.tables:
        logger.warning("%s: saved tables have no content blocks with source locations", paper_id)
        return "unsupported"
    if not tables:
        return "no_tables"
    if not row_count:
        return "unsupported"
    return "partial" if issues else "ready"


def scan_approved_tables(
    service: Any, root_folder_id: str, paper_id: str | None = None
) -> Counter[str]:
    """Read direct OpenAlex paper folders and report candidates without writing to Drive."""
    drive_storage.verify_named_folder(service, root_folder_id, _APPROVED_FOLDER_NAME)
    logger.info("Verified Drive folder %s (%s)", _APPROVED_FOLDER_NAME, root_folder_id)
    folders: defaultdict[str, list[str]] = defaultdict(list)
    for item in drive_storage.list_subfolders(service, root_folder_id):
        name = item["name"]
        if fullmatch(r"W[0-9]+", name) and (paper_id is None or name == paper_id):
            folders[name].append(item["id"])
    if not folders:
        raise ValueError(f"No matching approved paper folders found for {paper_id or 'the scan'}")

    counts: Counter[str] = Counter()
    for name, folder_ids in sorted(folders.items()):
        try:
            if len(folder_ids) != 1:
                raise ValueError(f"Found {len(folder_ids)} folders with the same paper ID")
            counts[_scan_paper(service, name, folder_ids[0])] += 1
        except HttpError as error:
            # Auth, quota and server errors must stop rather than produce a misleading report.
            if error.resp.status != 404:
                raise
            logger.error("%s: paper folder or manifest could not be read (404)", name)
            counts["failed"] += 1
        except ValueError as error:
            logger.error("%s: %s", name, str(error).splitlines()[0])
            counts["failed"] += 1
    return counts


def main() -> None:
    """Run a bounded or explicitly folder-wide Drive dry run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder-id", required=True, help="Drive ID of the approved folder")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--paper-id", help="Scan one OpenAlex paper folder")
    selection.add_argument("--all", action="store_true", help="Scan every approved paper folder")
    args = parser.parse_args()
    if not fullmatch(r"[A-Za-z0-9_-]+", args.folder_id):
        parser.error("--folder-id must be a Drive ID, not a URL")
    if args.paper_id is not None and not fullmatch(r"W[0-9]+", args.paper_id):
        parser.error("--paper-id must be an OpenAlex ID such as W2618057564")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        settings = load_drive_connection_settings()
        service = drive_storage.build_drive_service(
            settings.service_account_file, settings.oauth_client_file, settings.oauth_token_file
        )
        counts = scan_approved_tables(service, args.folder_id, args.paper_id)
    except (OSError, RuntimeError, HttpError, ValueError) as error:
        parser.exit(1, f"Drive dry run failed: {error}\n")
    logger.info("Dry run complete (no Drive writes): %s", dict(sorted(counts.items())))
    if counts["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

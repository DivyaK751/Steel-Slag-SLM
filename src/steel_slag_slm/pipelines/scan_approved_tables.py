"""Preview or apply contextual table enrichment on approved Drive manifests."""

import argparse
import json
import logging
from collections import Counter, defaultdict
from decimal import Decimal
from re import fullmatch
from typing import Any

from googleapiclient.errors import HttpError

from steel_slag_slm.config import load_drive_connection_settings
from steel_slag_slm.data import drive_storage
from steel_slag_slm.data.approved_table_text import enrich_approved_json
from steel_slag_slm.data.parsed_manifest import ParsedManifest

logger = logging.getLogger(__name__)
_APPROVED_FOLDER_NAME = "_parsed_approved"


def _scan_paper(service: Any, paper_id: str, folder_id: str, *, apply_root_id: str | None) -> str:
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

    updated = enrich_approved_json(source)
    enriched = ParsedManifest.model_validate_json(updated)
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
    if issues:
        return "partial"
    if apply_root_id is None:
        return "ready"

    updated_fields = json.loads(updated, parse_float=Decimal)
    updated_fields.pop("contextual_tables")
    if updated_fields != json.loads(source, parse_float=Decimal):
        raise ValueError("Enrichment would change existing JSON fields")
    drive_storage.verify_named_folder(service, apply_root_id, _APPROVED_FOLDER_NAME)
    drive_storage.verify_named_folder(service, folder_id, paper_id, parent_id=apply_root_id)
    if drive_storage.list_named_file_ids(service, folder_id, "parsed.json") != file_ids:
        raise ValueError("parsed.json moved or became ambiguous; refusing to overwrite it")
    logger.info("%s: updating existing parsed.json (Drive ID %s)", paper_id, file_ids[0])
    drive_storage.replace_existing_json(service, file_ids[0], source, updated)
    logger.info("%s: updated and verified %d rows; original fields unchanged", paper_id, row_count)
    return "updated"


def scan_approved_tables(
    service: Any, root_folder_id: str, paper_id: str | None = None, *, apply: bool = False
) -> Counter[str]:
    """Scan direct OpenAlex paper folders, updating complete candidates only when requested."""
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
            outcome = _scan_paper(
                service, name, folder_ids[0], apply_root_id=root_folder_id if apply else None
            )
            counts[outcome] += 1
        except HttpError as error:
            # Auth, quota and server errors must stop rather than produce a misleading report.
            if apply or error.resp.status != 404:
                raise
            logger.error("%s: paper folder or manifest could not be read (404)", name)
            counts["failed"] += 1
        except ValueError as error:
            logger.error("%s: %s", name, str(error).splitlines()[0])
            counts["failed"] += 1
    return counts


def main() -> None:
    """Run a one-paper or explicitly folder-wide Drive scan or backfill."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder-id", required=True, help="Drive ID of the approved folder")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--paper-id", help="Scan one OpenAlex paper folder")
    selection.add_argument("--all", action="store_true", help="Scan every approved paper folder")
    parser.add_argument("--apply", action="store_true", help="Update complete candidates in Drive")
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
        counts = scan_approved_tables(service, args.folder_id, args.paper_id, apply=args.apply)
    except (OSError, RuntimeError, HttpError, ValueError) as error:
        parser.exit(1, f"Approved Drive job failed: {error}\n")
    mode = "Apply" if args.apply else "Dry run (no Drive writes)"
    logger.info("%s complete: %s", mode, dict(sorted(counts.items())))
    if counts["failed"]:
        raise SystemExit(1)
    if args.apply and args.paper_id and not counts["updated"] and not counts["already_has_field"]:
        parser.exit(1, "Pilot was not eligible for an update; inspect the scan result.\n")


if __name__ == "__main__":
    main()

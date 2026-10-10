"""Create accepted-paper manifests, download PDFs, and optionally sync to Drive."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import config
from .drive_upload import upload_pdfs
from .io_utils import download_pdf, is_valid_pdf, load_csv, openalex_work_filename, save_csv

LOGGER = logging.getLogger(__name__)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _pdf_urls(record: Mapping[str, Any]) -> list[str]:
    """Get candidate PDF links from OpenAlex OA and primary-location metadata."""
    open_access = _as_mapping(record.get("open_access"))
    primary_location = _as_mapping(record.get("primary_location"))
    urls = (
        open_access.get("oa_url"),
        primary_location.get("pdf_url"),
    )
    return list(dict.fromkeys(url for url in urls if isinstance(url, str) and url))


def _paper_id(record: Mapping[str, Any]) -> str | None:
    value = record.get("id") or record.get("openalex_id")
    return value if isinstance(value, str) and value.strip() else None


def _prepare_accepted_papers(
    records: list[dict[str, Any]],
    pdf_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Download/validate accepted papers and return manifest, manual list, counts."""
    relevant: list[dict[str, Any]] = []
    manual: list[dict[str, Any]] = []
    stats = {"accepted": 0, "pdf_ready": 0, "download_failed_or_unavailable": 0}

    for record in records:
        if str(record.get("decision", "")).strip() != "Yes":
            continue

        stats["accepted"] += 1
        item = dict(record)
        work_id = _paper_id(record)
        if not work_id:
            item["access_resolution_note"] = "Missing OpenAlex work ID."
            manual.append(item)
            stats["download_failed_or_unavailable"] += 1
            continue

        try:
            filename = openalex_work_filename(work_id)
        except ValueError as error:
            item["access_resolution_note"] = str(error)
            manual.append(item)
            stats["download_failed_or_unavailable"] += 1
            continue

        local_path = pdf_dir / filename
        if local_path.is_file():
            with local_path.open("rb") as pdf_file:
                valid_existing = is_valid_pdf(pdf_file.read(5))
            if not valid_existing:
                local_path.unlink()

        if not local_path.is_file():
            errors: list[str] = []
            for url in _pdf_urls(record):
                try:
                    download_pdf(url, local_path)
                    break
                except Exception as error:  # Preserve the row and try another OA link.
                    errors.append(f"{url}: {error}")
                    LOGGER.warning("PDF download failed for %s: %s", work_id, error)

        if local_path.is_file():
            with local_path.open("rb") as pdf_file:
                valid_pdf = is_valid_pdf(pdf_file.read(5))
        else:
            valid_pdf = False

        item["local_pdf_file"] = filename if valid_pdf else ""
        relevant.append(item)
        if valid_pdf:
            stats["pdf_ready"] += 1
        else:
            note = "No usable open-access PDF URL was available."
            if errors:
                note += " Download attempts: " + " | ".join(errors)
            item["access_resolution_note"] = note
            manual.append(item)
            stats["download_failed_or_unavailable"] += 1

    return relevant, manual, stats


def run_delivery(
    input_path: Path,
    pdf_dir: Path = config.PDF_DIR,
    sync_drive: bool = False,
) -> dict[str, Any]:
    """Build delivery CSVs from classifier output and optionally sync PDFs to Drive."""
    records = load_csv(input_path)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    relevant, manual, stats = _prepare_accepted_papers(records, pdf_dir)
    unclear = [
        dict(record)
        for record in records
        if str(record.get("decision", "")).strip() == "Unclear"
    ]

    save_csv(relevant, config.OUTPUT_DIR / "relevant_papers.csv")
    save_csv(manual, config.OUTPUT_DIR / "needs_manual_access.csv")
    save_csv(unclear, config.OUTPUT_DIR / "unclear_papers.csv")

    if sync_drive:
        stats["drive"] = upload_pdfs(pdf_dir, config.DRIVE_FOLDER_ID)

    return {**stats, "unclear": len(unclear)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=config.OUTPUT_DIR / "classified.csv",
        help="Classifier output CSV containing a decision column.",
    )
    parser.add_argument(
        "--pdf-dir",
        type=Path,
        default=config.PDF_DIR,
        help="Local destination for PDFs named by OpenAlex work ID.",
    )
    parser.add_argument(
        "--upload-to-drive",
        action="store_true",
        help="After local delivery, sync valid PDFs to the configured shared-drive folder.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    stats = run_delivery(args.input, args.pdf_dir, args.upload_to_drive)
    print(f"Delivery complete: {stats}")


if __name__ == "__main__":
    main()

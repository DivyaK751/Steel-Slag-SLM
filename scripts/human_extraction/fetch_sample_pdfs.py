"""Download the PDFs of the human-extraction sample and start its status tracker.

Reads every paper in `<project>.steel_slag.human_extracted_data_papers`, finds its PDF in the
Drive folder set by DRIVE_FOLDER_ID, and saves it to human_extracted_data/pdfs/ (git-ignored).

Run: uv run python scripts/human_extraction/fetch_sample_pdfs.py
"""

import csv
from pathlib import Path

from google.cloud import bigquery

from steel_slag_slm.config import PROJECT_ROOT, load_drive_connection_settings, load_gcp_settings
from steel_slag_slm.data import drive_storage

SAMPLE_TABLE = "steel_slag.human_extracted_data_papers"
OUTPUT_DIR = PROJECT_ROOT / "human_extracted_data"
TRACKER_COLUMNS = [
    "#",
    "openalex_id",
    "title",
    "year",
    "pdf_downloaded",
    "extraction_status",
    "notes",
]


def _read_sample(client: bigquery.Client, project: str) -> list[bigquery.Row]:
    query = f"SELECT openalex_id, title, year FROM `{project}.{SAMPLE_TABLE}` ORDER BY openalex_id"
    return list(client.query(query).result())


def _download_missing(service: object, file_id: str, destination: Path) -> None:
    # Re-runs skip PDFs already on disk
    if not destination.exists():
        destination.write_bytes(drive_storage.download_pdf(service, file_id))


def main() -> None:
    gcp = load_gcp_settings()
    drive = load_drive_connection_settings()
    service = drive_storage.build_drive_service(
        drive.service_account_file, drive.oauth_client_file, drive.oauth_token_file
    )
    papers = _read_sample(bigquery.Client(project=gcp.project), gcp.project)
    drive_files = drive_storage.list_paper_files(service, drive.folder_id)

    pdf_dir = OUTPUT_DIR / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for number, paper in enumerate(papers, start=1):
        file_id = drive_files.get(paper.openalex_id)
        if file_id:
            _download_missing(service, file_id, pdf_dir / f"{paper.openalex_id}.pdf")
        rows.append(
            {
                "#": number,
                "openalex_id": paper.openalex_id,
                "title": paper.title,
                "year": paper.year,
                "pdf_downloaded": "Yes" if file_id else "No",
                "extraction_status": "Not started" if file_id else "Blocked - PDF missing",
                "notes": "",
            }
        )

    with (OUTPUT_DIR / "status_tracker.csv").open("w", newline="") as tracker:
        writer = csv.DictWriter(tracker, fieldnames=TRACKER_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    missing = [row["openalex_id"] for row in rows if row["pdf_downloaded"] == "No"]
    print(f"Downloaded {len(rows) - len(missing)}/{len(rows)} PDFs; missing: {missing or 'none'}")


if __name__ == "__main__":
    main()

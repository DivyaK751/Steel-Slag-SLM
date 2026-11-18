"""Download configured parsing-diagnostic PDFs from a Google Drive folder."""

import argparse
import os
from pathlib import Path

import pymupdf
from dotenv import load_dotenv

from steel_slag_slm.config import DEFAULT_GOOGLE_TOKEN_PATH, PROJECT_ROOT
from steel_slag_slm.data.drive_storage import (
    build_drive_service as create_drive_service,
    download_pdf,
    list_paper_files,
)
from check_parsing_diagnostic_pdfs import required_paper_ids

PDF_DIR = PROJECT_ROOT / "data/interim/pdf_parsing/parsing_diagnostics/pdfs"


def _configured_path(name: str) -> Path | None:
    """Read an optional credential path from the environment."""
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser() if value else None


def _build_drive_service():
    """Connect to Drive using the repository's local OAuth configuration."""
    load_dotenv(PROJECT_ROOT / ".env")

    service_account_file = _configured_path("GOOGLE_APPLICATION_CREDENTIALS")
    if service_account_file and not service_account_file.is_file():
        raise FileNotFoundError(
            f"GOOGLE_APPLICATION_CREDENTIALS points to a missing file: {service_account_file}"
        )

    token_file = Path(
        os.environ.get("GOOGLE_OAUTH_TOKEN_FILE", "").strip() or DEFAULT_GOOGLE_TOKEN_PATH
    ).expanduser()
    oauth_client_file = _configured_path("GOOGLE_OAUTH_CLIENT_FILE")

    if oauth_client_file and not oauth_client_file.is_file():
        if not token_file.is_file():
            raise FileNotFoundError(
                "The configured GOOGLE_OAUTH_CLIENT_FILE is missing and no saved Drive login "
                f"exists at {token_file}. Put your authorized OAuth client JSON at the configured "
                "path, or correct GOOGLE_OAUTH_CLIENT_FILE in .env."
            )
        oauth_client_file = None

    return create_drive_service(service_account_file, oauth_client_file, token_file)


def _validate_pdf(content: bytes, paper_id: str) -> None:
    """Reject a Drive download that cannot be opened as a PDF."""
    with pymupdf.open(stream=content, filetype="pdf") as document:
        if document.page_count == 0:
            raise ValueError(f"{paper_id}: downloaded PDF has no pages")


def _save_pdf(output_path: Path, content: bytes) -> None:
    """Write a PDF atomically so an interrupted download cannot leave a partial file."""
    temporary_path = output_path.with_name(f".{output_path.name}.partial")
    try:
        temporary_path.write_bytes(content)
        temporary_path.replace(output_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def main() -> int:
    """Preview Drive matches or download the configured diagnostic PDFs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--drive-folder-id",
        required=True,
        help="Google Drive folder ID (the part after /folders/ in its URL)",
    )
    parser.add_argument("--pdf-dir", type=Path, default=PDF_DIR)
    parser.add_argument(
        "--download",
        action="store_true",
        help="download matched files; without this flag, only preview the matches",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace existing local PDFs (requires --download)",
    )
    args = parser.parse_args()

    if args.overwrite and not args.download:
        parser.error("--overwrite requires --download")
    if not args.pdf_dir.is_dir():
        parser.error(f"PDF destination folder does not exist: {args.pdf_dir}")

    paper_ids = required_paper_ids()
    drive_service = _build_drive_service()
    drive_files = list_paper_files(drive_service, args.drive_folder_id)

    matched_ids = sorted(paper_ids & drive_files.keys())
    missing_ids = sorted(paper_ids - drive_files.keys())
    print(f"Required papers: {len(paper_ids)}")
    print(f"Found in Drive: {len(matched_ids)}")
    print(f"Not found in Drive: {len(missing_ids)}")
    for paper_id in missing_ids:
        print(f"  Missing: {paper_id}")

    if not args.download:
        print("Preview only; pass --download to save matched PDFs.")
        return 1 if missing_ids else 0

    downloaded = 0
    already_present = 0
    for paper_id in matched_ids:
        output_path = args.pdf_dir / f"{paper_id}.pdf"
        if output_path.exists() and not args.overwrite:
            print(f"Already present: {output_path.name}")
            already_present += 1
            continue

        content = download_pdf(drive_service, drive_files[paper_id])
        _validate_pdf(content, paper_id)
        _save_pdf(output_path, content)
        print(f"Downloaded: {output_path.name}")
        downloaded += 1

    print(f"Downloaded: {downloaded}; already present: {already_present}")
    return 1 if missing_ids else 0


if __name__ == "__main__":
    raise SystemExit(main())
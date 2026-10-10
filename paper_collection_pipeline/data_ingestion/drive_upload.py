"""Optional Google Shared Drive sync for already-downloaded PDFs.

The local pipeline remains usable without Google dependencies or Drive access.
Run this explicitly only after accepted-paper PDFs are present locally.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from . import config
from .io_utils import is_valid_pdf

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"


def _build_drive_service() -> Any:
    """Authenticate with Colab user auth or local Google Application Default Credentials."""
    try:
        from google.auth import default
        from googleapiclient.discovery import build
    except ImportError as error:
        raise RuntimeError(
            "Drive upload dependencies are missing. Install requirements-drive.txt."
        ) from error

    try:
        from google.colab import auth as colab_auth
    except ImportError:
        colab_auth = None

    if colab_auth is not None:
        colab_auth.authenticate_user()

    credentials, _ = default(scopes=[DRIVE_SCOPE])
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def list_existing_names(service: Any, folder_id: str) -> set[str]:
    """List all filenames in the destination folder, including shared-drive files."""
    names: set[str] = set()
    page_token: str | None = None

    while True:
        response = (
            service.files()
            .list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken,files(name)",
                pageSize=1000,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                corpora="allDrives",
            )
            .execute()
        )
        names.update(
            item["name"] for item in response.get("files", []) if item.get("name")
        )
        page_token = response.get("nextPageToken")
        if not page_token:
            return names


def upload_pdfs(
    pdf_dir: str | Path = config.PDF_DIR,
    folder_id: str | None = config.DRIVE_FOLDER_ID,
    service: Any | None = None,
) -> dict[str, int]:
    """Upload valid local PDFs, skipping names already present in the Drive folder."""
    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError as error:
        raise RuntimeError(
            "Drive upload dependencies are missing. Install requirements-drive.txt."
        ) from error

    directory = Path(pdf_dir)
    if not directory.is_dir():
        raise FileNotFoundError(f"Local PDF directory does not exist: {directory}")
    if not folder_id:
        raise RuntimeError(
            "Set STEEL_SLAG_DRIVE_FOLDER_ID to the team's shared-drive folder ID "
            "before uploading."
        )
    if service is None:
        service = _build_drive_service()

    existing = list_existing_names(service, folder_id)
    stats = {"uploaded": 0, "already_present": 0, "invalid_pdf": 0}

    for pdf_path in sorted(directory.glob("*.pdf")):
        if pdf_path.name in existing:
            stats["already_present"] += 1
            continue

        with pdf_path.open("rb") as pdf_file:
            if not is_valid_pdf(pdf_file.read(5)):
                stats["invalid_pdf"] += 1
                continue

        media = MediaFileUpload(str(pdf_path), mimetype="application/pdf", resumable=True)
        (
            service.files()
            .create(
                body={"name": pdf_path.name, "parents": [folder_id]},
                media_body=media,
                fields="id,name",
                supportsAllDrives=True,
            )
            .execute()
        )
        existing.add(pdf_path.name)
        stats["uploaded"] += 1

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir", type=Path, default=config.PDF_DIR)
    parser.add_argument("--folder-id", default=config.DRIVE_FOLDER_ID)
    args = parser.parse_args()

    stats = upload_pdfs(args.pdf_dir, args.folder_id)
    print(
        "Drive sync complete: "
        f"uploaded={stats['uploaded']}, "
        f"already_present={stats['already_present']}, "
        f"invalid_pdf={stats['invalid_pdf']}"
    )


if __name__ == "__main__":
    main()

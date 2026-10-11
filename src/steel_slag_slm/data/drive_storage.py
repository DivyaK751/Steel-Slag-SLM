"""Read and write paper PDFs in the team's shared Google Drive folder."""

import io
import re
from typing import Any

import google.auth
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from steel_slag_slm.schemas import Paper

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
MAX_TITLE_CHARS_IN_NAME = 120
# Shared-drive flags every call needs, or Drive silently ignores shared drives
_ALL_DRIVES = {"supportsAllDrives": True}
_LIST_ALL_DRIVES = {**_ALL_DRIVES, "includeItemsFromAllDrives": True}

_OPENALEX_ID_PATTERN = re.compile(r"^(W\d+)")


def build_drive_service() -> Any:
    """Create a Drive client from Application Default Credentials or a service account key."""
    credentials, _ = google.auth.default(scopes=DRIVE_SCOPES)
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def build_file_name(paper: Paper) -> str:
    """Name a PDF '<OpenAlex ID> - <title>.pdf' so people can browse the folder by title."""
    safe_title = re.sub(r'[\\/:*?"<>|\s]+', " ", paper.title).strip()
    return f"{paper.openalex_id} - {safe_title[:MAX_TITLE_CHARS_IN_NAME]}.pdf"


def parse_openalex_id(file_name: str) -> str | None:
    """Return the OpenAlex ID a file name starts with, e.g. 'W123.pdf' -> 'W123'."""
    match = _OPENALEX_ID_PATTERN.match(file_name)
    return match.group(1) if match else None


def _list_children(service: Any, folder_id: str, extra_query: str = "") -> list[dict[str, str]]:
    """List every non-trashed item directly inside a folder, following pagination."""
    query = f"'{folder_id}' in parents and trashed = false{extra_query}"
    items: list[dict[str, str]] = []
    page_token = None
    while True:
        response = service.files().list(
            q=query, fields="nextPageToken, files(id, name)", pageSize=1000,
            pageToken=page_token, **_LIST_ALL_DRIVES,
        ).execute()
        items.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            return items


def _find_subfolder_id(service: Any, parent_id: str, name: str) -> str | None:
    escaped_name = name.replace("'", "\\'")
    query = f" and mimeType = '{FOLDER_MIME_TYPE}' and name = '{escaped_name}'"
    matches = _list_children(service, parent_id, query)
    return matches[0]["id"] if matches else None


def list_known_paper_ids(service: Any, folder_id: str, rejected_subfolder_name: str) -> set[str]:
    """Return OpenAlex IDs already in the folder or in its rejected subfolder."""
    folder_ids = [folder_id]
    rejected_id = _find_subfolder_id(service, folder_id, rejected_subfolder_name)
    if rejected_id:
        folder_ids.append(rejected_id)
    names = [item["name"] for fid in folder_ids for item in _list_children(service, fid)]
    return {paper_id for name in names if (paper_id := parse_openalex_id(name))}


def has_paper(service: Any, folder_id: str, openalex_id: str) -> bool:
    """Check whether a teammate's run uploaded this paper since we listed the folder."""
    return bool(_list_children(service, folder_id, f" and name contains '{openalex_id}'"))


def upload_pdf(service: Any, folder_id: str, paper: Paper, pdf: bytes, pdf_source: str) -> str:
    """Upload a PDF with its metadata attached, returning the new Drive file ID."""
    metadata = {
        "name": build_file_name(paper),
        "parents": [folder_id],
        "description": f"{paper.title}\nDOI: {paper.doi or 'n/a'}\nYear: {paper.year or 'n/a'}",
        # appProperties are searchable later without opening the PDF
        "appProperties": {
            "openalex_id": paper.openalex_id,
            "year": str(paper.year or ""),
            "pdf_source": pdf_source,
        },
    }
    media = MediaIoBaseUpload(io.BytesIO(pdf), mimetype="application/pdf", resumable=True)
    created = service.files().create(
        body=metadata, media_body=media, fields="id", **_ALL_DRIVES
    ).execute()
    return str(created["id"])

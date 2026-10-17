"""Read and write paper PDFs in the team's shared Google Drive folder."""

import io
import re
from pathlib import Path
from typing import Any

from google.auth.credentials import Credentials
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from steel_slag_slm.schemas import Paper

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
MAX_TITLE_CHARS_IN_NAME = 120
# Shared-drive flags every call needs, or Drive silently ignores shared drives
_ALL_DRIVES = {"supportsAllDrives": True}
_LIST_ALL_DRIVES = {**_ALL_DRIVES, "includeItemsFromAllDrives": True}
# Retries Google's temporary errors (5xx, rate limits) with backoff before giving up
DRIVE_RETRIES = 3

_OPENALEX_ID_PATTERN = re.compile(r"^(W\d+)")


def _load_user_credentials(oauth_client_file: Path | None, token_file: Path) -> Credentials:
    """Sign in with a personal Google account, reusing the saved login when possible.

    Raises:
        RuntimeError: If the saved login can't be refreshed and no OAuth client file is
            available to open a browser sign-in (e.g. on GitHub Actions).
    """
    credentials: UserCredentials | None = None
    if token_file.is_file():
        credentials = UserCredentials.from_authorized_user_file(str(token_file), DRIVE_SCOPES)
    if credentials and credentials.valid:
        return credentials
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    elif oauth_client_file:
        # Opens a browser tab once; later runs reuse the saved token
        flow = InstalledAppFlow.from_client_secrets_file(str(oauth_client_file), DRIVE_SCOPES)
        credentials = flow.run_local_server(port=0)
    else:
        raise RuntimeError(
            f"The saved Google login at {token_file} is missing or no longer valid. "
            "Sign in again locally with `uv run fetch-papers`, then update the "
            "GOOGLE_OAUTH_TOKEN_JSON secret."
        )
    assert credentials is not None
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(credentials.to_json())
    return credentials


def build_drive_service(
    service_account_file: Path | None, oauth_client_file: Path | None, token_file: Path
) -> Any:
    """Create a Drive client from a service account key or, failing that, a personal login.

    A saved personal login (token_file) works on its own, which is how GitHub Actions signs in.

    Raises:
        ValueError: If no service account key, OAuth client file, or saved login is available.
    """
    credentials: Credentials
    if service_account_file:
        credentials = service_account.Credentials.from_service_account_file(
            str(service_account_file), scopes=DRIVE_SCOPES
        )
    elif oauth_client_file or token_file.is_file():
        credentials = _load_user_credentials(oauth_client_file, token_file)
    else:
        raise ValueError(
            "No Google sign-in configured: set GOOGLE_OAUTH_CLIENT_FILE (your own account) "
            "or GOOGLE_APPLICATION_CREDENTIALS (service account) in .env"
        )
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
        response = (
            service.files()
            .list(
                q=query,
                fields="nextPageToken, files(id, name)",
                pageSize=1000,
                pageToken=page_token,
                **_LIST_ALL_DRIVES,
            )
            .execute(num_retries=DRIVE_RETRIES)
        )
        items.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            return items


def _find_child_id(service: Any, parent_id: str, name: str, extra_query: str = "") -> str | None:
    escaped_name = name.replace("'", "\\'")
    matches = _list_children(service, parent_id, f" and name = '{escaped_name}'{extra_query}")
    return matches[0]["id"] if matches else None


def download_pdf(service: Any, file_id: str) -> bytes:
    """Return the raw bytes of a PDF stored in Drive."""
    content: bytes = (
        service.files()
        .get_media(fileId=file_id, **_ALL_DRIVES)
        .execute(num_retries=DRIVE_RETRIES)
    )
    return content


def ensure_subfolder(service: Any, parent_id: str, name: str) -> str:
    """Return the ID of the named subfolder, creating it if it does not exist."""
    existing = _find_child_id(
        service,
        parent_id,
        name,
        f" and mimeType = '{FOLDER_MIME_TYPE}'",
    )
    if existing:
        return existing

    created = (
        service.files()
        .create(
            body={
                "name": name,
                "parents": [parent_id],
                "mimeType": FOLDER_MIME_TYPE,
            },
            fields="id",
            **_ALL_DRIVES,
        )
        .execute(num_retries=DRIVE_RETRIES)
    )
    return str(created["id"])


def list_known_paper_ids(service: Any, folder_id: str, rejected_subfolder_name: str) -> set[str]:
    """Return OpenAlex IDs already in the folder or in its rejected subfolder."""
    folder_ids = [folder_id]
    rejected_id = _find_child_id(
        service, folder_id, rejected_subfolder_name, f" and mimeType = '{FOLDER_MIME_TYPE}'"
    )
    if rejected_id:
        folder_ids.append(rejected_id)
    names = [item["name"] for fid in folder_ids for item in _list_children(service, fid)]
    return {paper_id for name in names if (paper_id := parse_openalex_id(name))}


def list_paper_files(service: Any, folder_id: str) -> dict[str, str]:
    """Map each OpenAlex ID in the folder (not its subfolders) to its Drive file ID."""
    return {
        paper_id: item["id"]
        for item in _list_children(service, folder_id, " and mimeType = 'application/pdf'")
        if (paper_id := parse_openalex_id(item["name"]))
    }


def has_paper(service: Any, folder_id: str, openalex_id: str) -> bool:
    """Check whether a teammate's run uploaded this paper since we listed the folder."""
    matches = _list_children(service, folder_id, f" and name contains '{openalex_id}'")
    # "contains" also matches longer IDs (W12 matches W123), so compare exactly
    return any(parse_openalex_id(item["name"]) == openalex_id for item in matches)


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
    created = (
        service.files()
        .create(body=metadata, media_body=media, fields="id", **_ALL_DRIVES)
        .execute(num_retries=DRIVE_RETRIES)
    )
    return str(created["id"])


def read_text_file(service: Any, folder_id: str, file_name: str) -> str:
    """Read a text file in the folder; empty if the file doesn't exist yet."""
    file_id = _find_child_id(service, folder_id, file_name)
    if not file_id:
        return ""
    content: bytes = (
        service.files().get_media(fileId=file_id, **_ALL_DRIVES).execute(num_retries=DRIVE_RETRIES)
    )
    return content.decode()


def write_text_file(service: Any, folder_id: str, file_name: str, text: str) -> None:
    """Save a text file in the folder, replacing its previous contents."""
    media = MediaIoBaseUpload(io.BytesIO(text.encode()), mimetype="text/plain")
    file_id = _find_child_id(service, folder_id, file_name)
    if file_id:
        service.files().update(fileId=file_id, media_body=media, **_ALL_DRIVES).execute(
            num_retries=DRIVE_RETRIES
        )
    else:
        body = {"name": file_name, "parents": [folder_id]}
        service.files().create(body=body, media_body=media, **_ALL_DRIVES).execute(
            num_retries=DRIVE_RETRIES
        )


def read_id_list(service: Any, folder_id: str, file_name: str) -> set[str]:
    """Read IDs from a one-per-line text file in the folder; empty if the file doesn't exist."""
    text = read_text_file(service, folder_id, file_name)
    return {line.strip() for line in text.splitlines() if line.strip()}


def write_id_list(service: Any, folder_id: str, file_name: str, ids: set[str]) -> None:
    """Save IDs one per line to a text file in the folder, replacing its previous contents."""
    write_text_file(service, folder_id, file_name, "\n".join(sorted(ids)))

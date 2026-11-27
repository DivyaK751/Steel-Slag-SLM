"""Find the Drive folders the commands and the chat API read: parsed papers and their index."""

from typing import Any

from steel_slag_slm.config import (
    load_drive_connection_settings,
    load_embedding_settings,
    load_pdf_parsing_settings,
)
from steel_slag_slm.data.drive_storage import (
    build_drive_service,
    ensure_subfolder,
    find_subfolder_id,
)


def connect_to_parsed_folder(folder_name: str | None = None) -> tuple[Any, str]:
    """Connect to the configured parsed Drive folder, optionally overriding its name."""
    drive_settings = load_drive_connection_settings()
    drive_service = build_drive_service(
        drive_settings.service_account_file,
        drive_settings.oauth_client_file,
        drive_settings.oauth_token_file,
    )
    parsed_folder_id = find_subfolder_id(
        drive_service,
        drive_settings.folder_id,
        folder_name if folder_name is not None else load_pdf_parsing_settings().output_folder_name,
    )
    if parsed_folder_id is None:
        raise SystemExit("Configured parsed folder is missing in Drive; run parse-pdfs first")
    return drive_service, parsed_folder_id


def index_folder_id(drive_service: Any, parsed_folder_name: str | None) -> str:
    """Return the folder holding this parsed folder's search index, creating it if needed.

    It sits in the shared Drive folder (<index_folder_name>/<parsed folder name>/), which
    everyone on the project can write to, unlike a parsed folder owned by one person.
    """
    name = parsed_folder_name or load_pdf_parsing_settings().output_folder_name
    root_id = load_drive_connection_settings().folder_id
    index_root = ensure_subfolder(
        drive_service, root_id, load_embedding_settings().index_folder_name
    )
    return ensure_subfolder(drive_service, index_root, name)

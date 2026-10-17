from pathlib import Path
from unittest.mock import MagicMock

import pytest

from steel_slag_slm.data.drive_storage import (
    build_drive_service,
    build_file_name,
    download_pdf,
    ensure_subfolder,
    has_paper,
    list_paper_files,
    parse_openalex_id,
    read_id_list,
    read_text_file,
)
from steel_slag_slm.schemas import Paper


def test_build_file_name_starts_with_id_and_strips_unsafe_characters() -> None:
    paper = Paper("W42", "CO2/H2O: a study?", "", None, None, None, None)
    assert build_file_name(paper) == "W42 - CO2 H2O a study.pdf"


def test_parse_openalex_id_reads_old_and_new_file_names() -> None:
    assert parse_openalex_id("W2068066265.pdf") == "W2068066265"
    assert parse_openalex_id("W42 - Some title.pdf") == "W42"
    assert parse_openalex_id("notes.docx") is None


def test_file_name_round_trips_to_its_id() -> None:
    paper = Paper("W123", "Steel slag binders", "", None, None, None, None)
    assert parse_openalex_id(build_file_name(paper)) == "W123"


def test_build_drive_service_requires_a_sign_in_method(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="No Google sign-in configured"):
        build_drive_service(None, None, tmp_path / "token.json")


def test_has_paper_ignores_longer_ids_with_the_same_prefix() -> None:
    service = MagicMock()
    service.files().list().execute.return_value = {"files": [{"id": "1", "name": "W123 - x.pdf"}]}
    assert not has_paper(service, "folder", "W12")
    assert has_paper(service, "folder", "W123")


def test_build_drive_service_without_client_file_fails_clearly_on_a_bad_saved_login(
    tmp_path: Path,
) -> None:
    token_file = tmp_path / "token.json"
    token_file.write_text('{"client_id": "x", "client_secret": "y", "refresh_token": ""}')
    with pytest.raises(RuntimeError, match="GOOGLE_OAUTH_TOKEN_JSON"):
        build_drive_service(None, None, token_file)


def test_list_paper_files_maps_ids_to_file_ids_and_skips_other_files() -> None:
    service = MagicMock()
    service.files().list().execute.return_value = {
        "files": [{"id": "f1", "name": "W1 - Slag.pdf"}, {"id": "f2", "name": "notes.pdf"}]
    }
    assert list_paper_files(service, "F") == {"W1": "f1"}


def test_read_text_file_is_empty_when_the_file_does_not_exist_yet() -> None:
    service = MagicMock()
    service.files().list().execute.return_value = {"files": []}
    assert read_text_file(service, "F", "_fetcher_queue_ids.txt") == ""
    assert read_id_list(service, "F", "_fetcher_queue_ids.txt") == set()


def test_download_pdf_returns_file_bytes() -> None:
    service = MagicMock()
    service.files().get_media().execute.return_value = b"%PDF-data"

    assert download_pdf(service, "pdf-file-id") == b"%PDF-data"


def test_ensure_subfolder_returns_existing_folder() -> None:
    service = MagicMock()
    service.files().list().execute.return_value = {
        "files": [{"id": "existing-id", "name": "_parsed"}]
    }

    assert ensure_subfolder(service, "parent-id", "_parsed") == "existing-id"
    service.files().create.assert_not_called()


def test_ensure_subfolder_creates_missing_folder() -> None:
    service = MagicMock()
    service.files().list().execute.return_value = {"files": []}
    service.files().create().execute.return_value = {"id": "new-id"}

    assert ensure_subfolder(service, "parent-id", "_parsed") == "new-id"
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from steel_slag_slm.data.drive_storage import (
    build_drive_service,
    build_file_name,
    has_paper,
    parse_openalex_id,
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

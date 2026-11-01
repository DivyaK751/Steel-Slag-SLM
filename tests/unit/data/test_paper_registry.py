from datetime import UTC, datetime
from unittest.mock import MagicMock

from steel_slag_slm.config import RegistrySettings
from steel_slag_slm.data import paper_registry
from steel_slag_slm.schemas import Paper

SETTINGS = RegistrySettings(dataset="steel_slag", table="papers", location="US")


def test_build_row_includes_language_and_drive_link() -> None:
    paper = Paper(
        "W1", "Slag cement", "", "https://doi.org/10.1/x", 2020, None, None,
        authors=("A. Author",), keywords=("steel slag",), language="ja",
    )  # fmt: skip
    row = paper_registry.build_row(paper, "FILE1", datetime(2026, 10, 16, tzinfo=UTC))
    assert row["language"] == "ja"
    assert row["authors"] == ["A. Author"] and row["keywords"] == ["steel slag"]
    assert row["drive_link"] == "https://drive.google.com/file/d/FILE1/view"
    assert row["abstract"] is None  # missing abstracts are stored as NULL, not ""
    assert row["abstract_source"] is None  # left for the abstract extractor
    assert row["added_at"].startswith("2026-10-16T00:00:00")


def test_ensure_papers_table_returns_full_table_id() -> None:
    client = MagicMock(project="proj")
    assert paper_registry.ensure_papers_table(client, SETTINGS) == "proj.steel_slag.papers"
    client.create_table.assert_called_once()


def test_list_registered_ids_reads_openalex_ids() -> None:
    client = MagicMock()
    client.query.return_value.result.return_value = [
        MagicMock(openalex_id="W1"),
        MagicMock(openalex_id="W2"),
    ]
    assert paper_registry.list_registered_ids(client, "p.d.t") == {"W1", "W2"}


def test_add_rows_skips_the_load_job_when_there_is_nothing_to_add() -> None:
    client = MagicMock()
    paper_registry.add_rows(client, "p.d.t", [])
    client.load_table_from_json.assert_not_called()
    paper_registry.add_rows(client, "p.d.t", [{"openalex_id": "W1"}])
    client.load_table_from_json.assert_called_once()

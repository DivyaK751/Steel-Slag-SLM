from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml
from pydantic import SecretStr

import steel_slag_slm.pipelines.sync_registry as sync_registry
from steel_slag_slm.config import BASE_CONFIG_PATH, Settings
from steel_slag_slm.data import drive_storage, paper_registry
from steel_slag_slm.exceptions import BudgetExhaustedError
from steel_slag_slm.schemas import Paper


@pytest.fixture
def settings() -> Settings:
    config = yaml.safe_load(BASE_CONFIG_PATH.read_text())
    return Settings(
        **config, openalex_api_key=SecretStr("k"), contact_email="a@b.edu", drive_folder_id="F"
    )


@pytest.fixture
def saved_rows(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Fake Drive holding W1-W3 and a registry that already has W1."""
    saved: list[dict[str, Any]] = []
    monkeypatch.setattr(
        drive_storage, "list_paper_files", lambda *_: {"W1": "f1", "W2": "f2", "W3": "f3"}
    )
    monkeypatch.setattr(paper_registry, "ensure_papers_table", lambda *_: "p.d.t")
    monkeypatch.setattr(paper_registry, "list_registered_ids", lambda *_: {"W1"})
    monkeypatch.setattr(paper_registry, "add_rows", lambda _c, _t, rows: saved.extend(rows))
    return saved


def _paper(paper_id: str) -> Paper:
    return Paper(paper_id, "t", "abstract", None, None, None, None, language="en")


def test_sync_looks_up_only_unregistered_papers(
    settings: Settings, saved_rows: list[dict[str, Any]]
) -> None:
    client = MagicMock()
    client.iter_papers_by_id.return_value = iter([_paper("W2"), _paper("W3")])
    outcomes = sync_registry.run_registry_sync(settings, MagicMock(), client, MagicMock())
    client.iter_papers_by_id.assert_called_once_with(["W2", "W3"])
    assert [(row["openalex_id"], row["drive_file_id"]) for row in saved_rows] == [
        ("W2", "f2"),
        ("W3", "f3"),
    ]
    assert outcomes == {"already_registered": 1, "added": 2, "not_added": 0}


def test_sync_skips_merged_ids_and_saves_rows_found_before_budget_ran_out(
    settings: Settings, saved_rows: list[dict[str, Any]]
) -> None:
    def papers(_ids: list[str]):  # type: ignore[no-untyped-def]
        yield _paper("W99")  # OpenAlex answered W2 with a merged work's new ID
        yield _paper("W3")
        raise BudgetExhaustedError("done")

    client = MagicMock()
    client.iter_papers_by_id.side_effect = papers
    outcomes = sync_registry.run_registry_sync(settings, MagicMock(), client, MagicMock())
    assert [row["openalex_id"] for row in saved_rows] == ["W3"]
    assert outcomes["not_added"] == 1

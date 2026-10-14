from unittest.mock import MagicMock

import pytest
import yaml
from pydantic import SecretStr

import steel_slag_slm.pipelines.fetch_papers as fetch_papers
from steel_slag_slm.config import BASE_CONFIG_PATH, Settings
from steel_slag_slm.data import drive_storage
from steel_slag_slm.exceptions import BudgetExhaustedError, OpenAlexRequestError
from steel_slag_slm.schemas import Paper


def _paper(paper_id: str, title: str = "Steel slag cement hydration") -> Paper:
    return Paper(paper_id, title, "", None, None, None, None)


@pytest.fixture
def settings() -> Settings:
    config = yaml.safe_load(BASE_CONFIG_PATH.read_text())
    config["fetch"]["queries"] = ["q1", "q2"]
    return Settings(
        **config, openalex_api_key=SecretStr("k"), contact_email="a@b.edu", drive_folder_id="F"
    )


saved_no_pdf: set[str] = set()


@pytest.fixture
def uploads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fake Drive that already holds W1 and records every upload."""
    uploaded: list[str] = []
    saved_no_pdf.clear()
    storage = drive_storage
    monkeypatch.setattr(storage, "list_known_paper_ids", lambda *_: {"W1"})
    monkeypatch.setattr(storage, "has_paper", lambda *_: False)
    monkeypatch.setattr(storage, "read_id_list", lambda *_: {"W9"})
    monkeypatch.setattr(storage, "write_id_list", lambda _s, _f, _n, ids: saved_no_pdf.update(ids))
    monkeypatch.setattr(
        storage, "upload_pdf", lambda _s, _f, paper, _pdf, _src: uploaded.append(paper.openalex_id)
    )
    return uploaded


def _client(results_by_query: dict[str, list[Paper]]) -> MagicMock:
    client = MagicMock()
    client.iter_papers.side_effect = lambda query: iter(results_by_query[query])
    client.download_pdf.return_value = (b"%PDF", "free")
    return client


def test_run_fetch_skips_known_irrelevant_and_repeated_papers(
    settings: Settings, uploads: list[str]
) -> None:
    client = _client(
        {
            "q1": [_paper("W1"), _paper("W2"), _paper("W3", "Steel slag asphalt mixture")],
            "q2": [_paper("W2"), _paper("W4")],
        }
    )
    outcomes = fetch_papers.run_fetch(settings, MagicMock(), client, max_new_papers=None)
    assert uploads == ["W2", "W4"]
    assert outcomes["already_known"] == 1 and outcomes["irrelevant"] == 1


def test_run_fetch_stops_cleanly_when_budget_runs_out(
    settings: Settings, uploads: list[str]
) -> None:
    client = _client({"q1": [_paper("W2"), _paper("W3")], "q2": []})
    client.download_pdf.side_effect = [(b"%PDF", "openalex"), BudgetExhaustedError("done")]
    outcomes = fetch_papers.run_fetch(settings, MagicMock(), client, max_new_papers=None)
    assert uploads == ["W2"]
    assert outcomes["added_openalex"] == 1


def test_run_fetch_respects_max_new_papers(settings: Settings, uploads: list[str]) -> None:
    client = _client({"q1": [_paper("W2"), _paper("W3")], "q2": [_paper("W4")]})
    fetch_papers.run_fetch(settings, MagicMock(), client, max_new_papers=2)
    assert uploads == ["W2", "W3"]


def test_run_fetch_remembers_papers_without_a_pdf(settings: Settings, uploads: list[str]) -> None:
    client = _client({"q1": [_paper("W9"), _paper("W2"), _paper("W3")], "q2": []})
    client.download_pdf.side_effect = [None, (b"%PDF", "free")]
    outcomes = fetch_papers.run_fetch(settings, MagicMock(), client, max_new_papers=None)
    assert uploads == ["W3"]
    assert outcomes["no_pdf_before"] == 1
    assert saved_no_pdf == {"W9", "W2"}


def test_run_fetch_skips_failed_download_without_marking_it_as_no_pdf(
    settings: Settings, uploads: list[str]
) -> None:
    client = _client({"q1": [_paper("W2"), _paper("W3")], "q2": []})
    client.download_pdf.side_effect = [OpenAlexRequestError("timed out"), (b"%PDF", "free")]
    outcomes = fetch_papers.run_fetch(settings, MagicMock(), client, max_new_papers=None)
    assert uploads == ["W3"]
    assert outcomes["download_error"] == 1
    assert "W2" not in saved_no_pdf


def test_run_fetch_moves_to_next_query_when_a_search_fails(
    settings: Settings, uploads: list[str]
) -> None:
    def iter_papers(query: str):  # type: ignore[no-untyped-def]
        if query == "q1":
            yield _paper("W2")
            raise OpenAlexRequestError("connection dropped")
        yield _paper("W4")

    client = _client({})
    client.iter_papers.side_effect = iter_papers
    fetch_papers.run_fetch(settings, MagicMock(), client, max_new_papers=None)
    assert uploads == ["W2", "W4"]

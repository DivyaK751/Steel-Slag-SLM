from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml
from pydantic import SecretStr

import steel_slag_slm.pipelines.fetch_papers as fetch_papers
from steel_slag_slm.config import BASE_CONFIG_PATH, Settings
from steel_slag_slm.data import drive_storage, paper_registry
from steel_slag_slm.data.openalex import SearchPages
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


class FakeDrive:
    """Drive folder holding W1, with W9 on the no-PDF list and the given queue and progress."""

    def __init__(self, settings: Settings, queue: set[str], progress: str = "q1\t1\nq2\t1") -> None:
        names = settings.drive
        self.files = {
            names.queue_list_name: "\n".join(sorted(queue)),
            names.no_pdf_list_name: "W9",
            names.search_progress_name: progress,
        }
        self.uploaded: list[str] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def read_text(_s: object, _f: str, name: str) -> str:
            return self.files.get(name, "")

        def write_text(_s: object, _f: str, name: str, text: str) -> None:
            self.files[name] = text

        def upload_pdf(_s: object, _f: str, paper: Paper, _pdf: bytes, _src: str) -> str:
            self.uploaded.append(paper.openalex_id)
            return f"file-{paper.openalex_id}"

        monkeypatch.setattr(drive_storage, "list_known_paper_ids", lambda *_: {"W1"})
        monkeypatch.setattr(drive_storage, "has_paper", lambda *_: False)
        monkeypatch.setattr(drive_storage, "read_text_file", read_text)
        monkeypatch.setattr(drive_storage, "write_text_file", write_text)
        monkeypatch.setattr(drive_storage, "upload_pdf", upload_pdf)

    def ids(self, name: str) -> set[str]:
        return set(self.files[name].split())


def _client(searches: dict[str, list[SearchPages]] | None = None) -> MagicMock:
    client = MagicMock()
    client.iter_papers_by_id.side_effect = lambda ids: iter(_paper(i) for i in ids)
    client.download_pdf.return_value = (b"%PDF", "free")
    pages = {query: iter(results) for query, results in (searches or {}).items()}
    client.read_search_pages.side_effect = lambda query, *_: next(pages[query])
    return client


def _run(settings: Settings, client: MagicMock, max_new_papers: int | None = None) -> Any:
    return fetch_papers.run_fetch(settings, MagicMock(), client, max_new_papers)


def test_run_fetch_downloads_queued_papers_before_searching(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    drive = FakeDrive(settings, queue={"W2", "W3", "W4"})
    drive.install(monkeypatch)
    client = _client()
    _run(settings, client, max_new_papers=2)
    assert drive.uploaded == ["W2", "W3"]
    assert drive.ids(settings.drive.queue_list_name) == {"W4"}
    client.read_search_pages.assert_not_called()


def test_run_fetch_searches_deeper_when_the_queue_is_empty(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    drive = FakeDrive(settings, queue=set(), progress="q1\t6\nq2\t1")
    drive.install(monkeypatch)
    client = _client(
        {
            "q1": [SearchPages([_paper("W1"), _paper("W2"), _paper("W9")], 11)],
            "q2": [SearchPages([_paper("W3", "Steel slag asphalt")], 6)],
        }
    )
    outcomes = _run(settings, client, max_new_papers=1)
    assert drive.uploaded == ["W2"]  # W1 in Drive, W9 has no PDF, W3 irrelevant
    assert outcomes["queued"] == 1
    assert drive.files[settings.drive.search_progress_name] == "q1\t11\nq2\t6"


def test_run_fetch_stops_once_every_search_has_nothing_new(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    drive = FakeDrive(settings, queue=set())
    drive.install(monkeypatch)
    client = _client({"q1": [SearchPages([], 1)], "q2": [SearchPages([_paper("W1")], 1)]})
    outcomes = _run(settings, client)
    assert outcomes["queued"] == 0
    assert client.read_search_pages.call_count == 2


def test_run_fetch_keeps_failed_downloads_queued_and_records_missing_pdfs(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    drive = FakeDrive(settings, queue={"W2", "W3", "W4"})
    drive.install(monkeypatch)
    client = _client({"q1": [SearchPages([], 1)], "q2": [SearchPages([], 1)]})
    client.download_pdf.side_effect = [OpenAlexRequestError("timeout"), None, (b"%PDF", "free")]
    outcomes = _run(settings, client)
    assert drive.uploaded == ["W4"]
    assert drive.ids(settings.drive.queue_list_name) == {"W2"}
    assert drive.ids(settings.drive.no_pdf_list_name) == {"W9", "W3"}
    assert outcomes["download_error"] == 1 and outcomes["no_pdf"] == 1


def test_run_fetch_saves_the_rest_of_the_queue_when_the_budget_runs_out(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    drive = FakeDrive(settings, queue={"W2", "W3", "W4"})
    drive.install(monkeypatch)
    client = _client()
    client.download_pdf.side_effect = [(b"%PDF", "openalex"), BudgetExhaustedError("done")]
    outcomes = _run(settings, client)
    assert outcomes["added_openalex"] == 1
    assert drive.ids(settings.drive.queue_list_name) == {"W3", "W4"}


def test_run_fetch_drops_queued_ids_openalex_no_longer_has(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    drive = FakeDrive(settings, queue={"W2", "W3"})
    drive.install(monkeypatch)
    client = _client()
    client.iter_papers_by_id.side_effect = lambda ids: iter([_paper("W3")])
    outcomes = _run(settings, client, max_new_papers=1)
    assert outcomes["not_found"] == 1
    assert drive.ids(settings.drive.queue_list_name) == set()


def test_run_fetch_adds_a_registry_row_for_each_uploaded_paper(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    drive = FakeDrive(settings, queue={"W2", "W3"})
    drive.install(monkeypatch)
    saved: list[dict[str, str]] = []
    monkeypatch.setattr(paper_registry, "ensure_papers_table", lambda *_: "p.d.t")
    monkeypatch.setattr(paper_registry, "add_rows", lambda _c, _t, rows: saved.extend(rows))
    fetch_papers.run_fetch(settings, MagicMock(), _client(), 2, bigquery_client=MagicMock())
    assert [(row["openalex_id"], row["drive_file_id"]) for row in saved] == [
        ("W2", "file-W2"),
        ("W3", "file-W3"),
    ]


def test_run_fetch_reads_newly_added_searches_first_and_forgets_removed_ones(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # q2 is new in the config and "old" was removed from it; the queue is not empty
    drive = FakeDrive(settings, queue={"W2"}, progress="q1\t16\nold\t4")
    drive.install(monkeypatch)
    client = _client({"q2": [SearchPages([_paper("W5")], 6)]})
    outcomes = _run(settings, client, max_new_papers=1)
    client.read_search_pages.assert_called_once_with("q2", 1, settings.fetch.pages_per_discovery)
    assert outcomes["queued"] == 1
    assert drive.uploaded == ["W2"]
    assert drive.files[settings.drive.search_progress_name] == "q1\t16\nq2\t6"


def test_run_fetch_saves_registry_rows_after_each_batch_not_twice(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    drive = FakeDrive(settings, queue={"W2", "W3"})
    drive.install(monkeypatch)
    batches: list[list[str]] = []
    monkeypatch.setattr(paper_registry, "ensure_papers_table", lambda *_: "p.d.t")
    monkeypatch.setattr(
        paper_registry,
        "add_rows",
        lambda _c, _t, rows: batches.append([row["openalex_id"] for row in rows]),
    )
    client = _client({"q1": [SearchPages([], 1)], "q2": [SearchPages([], 1)]})
    fetch_papers.run_fetch(settings, MagicMock(), client, None, bigquery_client=MagicMock())
    assert batches == [["W2", "W3"]]  # saved once after the batch; nothing left for the end

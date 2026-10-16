from unittest.mock import MagicMock

import pytest
import yaml
from pydantic import SecretStr

from steel_slag_slm.config import BASE_CONFIG_PATH, Settings
from steel_slag_slm.data.openalex import SearchPages
from steel_slag_slm.exceptions import OpenAlexRequestError
from steel_slag_slm.pipelines.discover_papers import discover_papers
from steel_slag_slm.schemas import Paper


@pytest.fixture
def settings() -> Settings:
    config = yaml.safe_load(BASE_CONFIG_PATH.read_text())
    config["fetch"]["queries"] = ["q1", "q2"]
    return Settings(
        **config, openalex_api_key=SecretStr("k"), contact_email="a@b.edu", drive_folder_id="F"
    )


def _paper(paper_id: str, title: str = "Steel slag cement hydration") -> Paper:
    return Paper(paper_id, title, "", None, None, None, None)


def test_discover_queues_new_relevant_papers_and_advances_each_search(
    settings: Settings,
) -> None:
    client = MagicMock()
    client.read_search_pages.side_effect = [
        SearchPages([_paper("W1"), _paper("W2"), _paper("W3", "Steel slag asphalt")], 11),
        SearchPages([_paper("W4"), _paper("W5")], 1),
    ]
    progress = {"q1": 6}
    queue = {"W5"}
    finished = discover_papers(settings, client, progress, skip_ids={"W1"}, queue=queue)
    assert queue == {"W2", "W4", "W5"}  # W1 known, W3 irrelevant, W5 already queued
    assert progress == {"q1": 11, "q2": 1}
    assert finished == {"q2"}
    client.read_search_pages.assert_any_call("q1", 6, settings.fetch.pages_per_discovery)
    client.read_search_pages.assert_any_call("q2", 1, settings.fetch.pages_per_discovery)


def test_discover_leaves_a_failed_search_where_it_was(settings: Settings) -> None:
    client = MagicMock()
    client.read_search_pages.side_effect = [OpenAlexRequestError("timeout"), SearchPages([], 6)]
    progress = {"q1": 3}
    discover_papers(settings, client, progress, skip_ids=set(), queue=set())
    assert progress == {"q1": 3, "q2": 6}

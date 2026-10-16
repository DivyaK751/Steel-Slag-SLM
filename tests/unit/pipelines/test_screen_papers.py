import asyncio
from typing import Any

import pytest

import steel_slag_slm.pipelines.screen_papers as screen_papers
from steel_slag_slm.config import ScreeningSettings
from steel_slag_slm.exceptions import AgentOutputError
from steel_slag_slm.llm.prompts import load_prompt
from steel_slag_slm.schemas import PaperForScreening, RelevanceDecision


class FakeTable:
    """In-memory papers table: tracks which IDs are still unscreened and what was written."""

    def __init__(self, ids: list[str]) -> None:
        self.unscreened = ids
        self.written: list[dict[str, Any]] = []

    def fetch(self, _client: Any, _table: str, limit: int, attempted: set[str]) -> list[Any]:
        todo = [i for i in self.unscreened if i not in attempted][:limit]
        return [PaperForScreening(i, f"title {i}", "BOF slag binder.", 2020, ()) for i in todo]

    def merge(self, _client: Any, _table: str, _key: str, rows: list[Any], _types: Any) -> int:
        self.written.extend(rows)
        self.unscreened = [i for i in self.unscreened if i not in {r["openalex_id"] for r in rows}]
        return len(rows)


@pytest.fixture
def settings() -> ScreeningSettings:
    return ScreeningSettings(
        model="m",
        temperature=0.0,
        prompt_name="screen_relevance",
        batch_size=2,
        max_concurrency=2,
        papers_table="p.d.t",
    )


def _install(monkeypatch: pytest.MonkeyPatch, table: FakeTable, failing: set[str]) -> None:
    async def fake_screen(_runner: Any, _prompt: Any, paper: PaperForScreening) -> Any:
        if paper.openalex_id in failing:
            raise AgentOutputError("bad json")
        return RelevanceDecision(
            openalex_id="W-wrong-echo",
            relevant="Yes",
            relevance_score=0.9,
            relevance_confidence="High",
            relevance_reason="r",
            supporting_excerpt="BOF slag binder.",
        )

    monkeypatch.setattr(screen_papers, "fetch_unscreened", table.fetch)
    monkeypatch.setattr(screen_papers, "merge_updates", table.merge)
    monkeypatch.setattr(screen_papers, "screen_paper", fake_screen)


def _run(settings: ScreeningSettings, max_papers: int | None, dry_run: bool = False) -> Any:
    prompt = load_prompt("screen_relevance")
    return asyncio.run(
        screen_papers.run_screening(settings, None, None, prompt, max_papers, dry_run)  # type: ignore[arg-type]
    )


def test_run_screening_writes_all_batches_keyed_by_paper_id(
    monkeypatch: pytest.MonkeyPatch, settings: ScreeningSettings
) -> None:
    table = FakeTable(["W1", "W2", "W3"])
    _install(monkeypatch, table, failing=set())
    outcomes = _run(settings, max_papers=None)
    assert outcomes["yes"] == 3 and outcomes["written"] == 3
    assert [row["openalex_id"] for row in table.written] == ["W1", "W2", "W3"]


def test_run_screening_skips_failed_papers_without_looping(
    monkeypatch: pytest.MonkeyPatch, settings: ScreeningSettings
) -> None:
    table = FakeTable(["W1", "W2", "W3"])
    _install(monkeypatch, table, failing={"W2"})
    outcomes = _run(settings, max_papers=None)
    assert outcomes["failed"] == 1 and outcomes["written"] == 2
    assert table.unscreened == ["W2"]


def test_run_screening_dry_run_writes_nothing_and_respects_max_papers(
    monkeypatch: pytest.MonkeyPatch, settings: ScreeningSettings
) -> None:
    table = FakeTable(["W1", "W2", "W3"])
    _install(monkeypatch, table, failing=set())
    outcomes = _run(settings, max_papers=3, dry_run=True)
    assert outcomes["yes"] == 3 and not table.written
    assert _run(settings, max_papers=1, dry_run=True)["yes"] == 1

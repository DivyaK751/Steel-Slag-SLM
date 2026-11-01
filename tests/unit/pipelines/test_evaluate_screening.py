import asyncio
from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook

import steel_slag_slm.pipelines.evaluate_screening as evaluate_screening
from steel_slag_slm.agents.runner import TokenUsage
from steel_slag_slm.config import EvaluationSettings, ModelPrice, ScreeningSettings
from steel_slag_slm.exceptions import AgentOutputError
from steel_slag_slm.llm.prompts import load_prompt
from steel_slag_slm.schemas import PaperForScreening, RelevanceDecision

LABELLED_ROWS = [
    {
        "openalex_id": "W1",
        "title": "a",
        "abstract": "x",
        "year": 2020,
        "keywords": "slag, cement",
        "human_label": "Yes",
    },
    {
        "openalex_id": "W2",
        "title": "b",
        "abstract": "x",
        "year": 2020,
        "keywords": None,
        "human_label": "no",
    },
    {
        "openalex_id": "W3",
        "title": "c",
        "abstract": "x",
        "year": 2020,
        "keywords": None,
        "human_label": None,
    },
    {
        "openalex_id": "W4",
        "title": "d",
        "abstract": "x",
        "year": 2020,
        "keywords": None,
        "human_label": "Unclear",
    },
]


def _run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, write: bool = False) -> Any:
    seen_keywords: dict[str, tuple[str, ...]] = {}

    async def fake_screen(_runner: Any, _prompt: Any, paper: PaperForScreening) -> Any:
        seen_keywords[paper.openalex_id] = paper.keywords
        if paper.openalex_id == "W4":
            raise AgentOutputError("bad json")
        decision = RelevanceDecision(
            relevant="Yes", relevance_score=0.9, relevance_confidence="High", relevance_reason="r"
        )
        return decision, TokenUsage(1_000_000, 100_000)

    monkeypatch.setattr(evaluate_screening, "query_rows", lambda *_: LABELLED_ROWS)
    monkeypatch.setattr(evaluate_screening, "screen_paper", fake_screen)
    screening = ScreeningSettings(
        model="m",
        temperature=0.0,
        prompt_name="screen_relevance",
        batch_size=10,
        max_concurrency=2,
        papers_table="p.d.papers",
    )
    evaluation = EvaluationSettings(
        labels_table="p.d.labels",
        output_dir=tmp_path,
        prices={"m": ModelPrice(input=0.5, output=2.0)},
    )
    merged: list[dict[str, Any]] = []

    def fake_merge(_client: Any, table: str, key: str, rows: list[Any], _types: Any) -> int:
        assert (table, key) == ("p.d.labels", "openalex_id")
        merged.extend(rows)
        return len(rows)

    monkeypatch.setattr(evaluate_screening, "merge_updates", fake_merge)
    path, metrics, updated = asyncio.run(
        evaluate_screening.run_evaluation(
            screening,
            evaluation,
            None,
            None,
            load_prompt("screen_relevance"),
            {"Model": "m"},  # type: ignore[arg-type]
            write,
        )
    )
    if write:
        assert updated == len(merged)
    return path, metrics, seen_keywords, merged


def test_run_evaluation_scores_only_labelled_papers_and_keeps_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path, metrics, seen_keywords, merged = _run(monkeypatch, tmp_path)
    assert not merged
    # W3 has no label; W4 failed and counts as a miss
    assert (metrics.labelled, metrics.matched) == (3, 1)
    assert seen_keywords["W1"] == ("slag", "cement")
    predictions = list(load_workbook(path)["Predictions"].iter_rows(min_row=2, values_only=True))
    assert [(row[0], row[3], row[4]) for row in predictions] == [
        ("W1", "Yes", "✓"),
        ("W2", "Yes", "✗"),
        ("W3", "Yes", None),  # Excel stores an empty cell as None
        ("W4", "Failed", "✗"),
    ]
    version = load_prompt("screen_relevance").version
    assert path.name.startswith("eval_") and path.name.endswith(f"_m_prompt-v{version}.xlsx")


def test_run_evaluation_reports_tokens_and_cost(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path, _metrics, _keywords, _merged = _run(monkeypatch, tmp_path)
    workbook = load_workbook(path)
    summary = {row[0]: row[1] for row in workbook["Summary"].iter_rows(values_only=True) if row}
    # 3 answered papers x (1M input at $0.50 + 0.1M output at $2.00) = 3 x $0.70
    assert summary["Input tokens"] == "3000000"
    assert summary["Estimated cost (USD)"] == "2.1000"
    assert summary["Estimated cost per paper (USD)"] == "0.70000"
    first_paper = next(workbook["Predictions"].iter_rows(min_row=2, values_only=True))
    assert first_paper[-1] == pytest.approx(0.7)


def test_run_evaluation_write_saves_answers_and_clears_failed_papers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _path, _metrics, _keywords, merged = _run(monkeypatch, tmp_path, write=True)
    by_id = {row["openalex_id"]: row for row in merged}
    assert set(by_id) == {"W1", "W2", "W3", "W4"}
    assert by_id["W1"]["relevant"] == "Yes"
    # W4 failed, so its columns are cleared rather than left from an older run
    assert by_id["W4"]["relevant"] is None and by_id["W4"]["relevance_reason"] is None

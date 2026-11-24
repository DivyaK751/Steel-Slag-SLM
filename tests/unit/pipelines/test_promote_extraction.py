from unittest.mock import MagicMock, patch

import pytest

from steel_slag_slm.data.extraction_tables import SHEET_TABLES
from steel_slag_slm.exceptions import PromotionError
from steel_slag_slm.pipelines import promote_extraction
from steel_slag_slm.schemas import ExtractionRun, JudgedCell, JudgedTable

TEST_IDS = ["W1", "W2"]
JUDGE = ("gemini-3.8-flash", 1)


def _run(run_id: str, paper_ids: list[str] = TEST_IDS) -> ExtractionRun:
    return ExtractionRun(run_id, "extract_data_v4", 4, "gemini-3.1-pro-preview", "test",
                         tuple(paper_ids))  # fmt: skip


def _evaluation(matches: int, misses: int) -> tuple[list[JudgedCell], list[JudgedTable]]:
    """Cells giving the wanted F1, and a judged entry for every sheet of every test paper."""
    cells = [JudgedCell("W1", "t", "c", 1, 1, "1", "1", "match")] * matches
    cells += [JudgedCell("W1", "t", "c", 1, 1, "1", "2", "mismatch")] * misses
    tables = [JudgedTable(p, s.table, 1, 1, 1, 1, 1) for p in TEST_IDS for s in SHEET_TABLES]
    return cells, tables


def _promote(runs: dict, evaluations: dict, champion: str | None, force: bool = False):  # type: ignore[no-untyped-def]
    with (
        patch.object(promote_extraction, "read_run", lambda _c, _d, run_id: runs.get(run_id)),
        patch.object(
            promote_extraction, "read_evaluation", lambda _c, _d, run_id, *_: evaluations[run_id]
        ),
        patch.object(promote_extraction, "latest_promoted_run_id", lambda *_: champion),
        patch.object(promote_extraction, "promote_run") as promote_run,
    ):
        result = promote_extraction.run_promotion(
            MagicMock(), "p.runs", "p.prod", "new", JUDGE, TEST_IDS, force
        )
    return result, promote_run


def test_first_run_is_promoted_without_a_champion() -> None:
    result, promote_run = _promote({"new": _run("new")}, {"new": _evaluation(3, 1)}, None)
    assert result.promoted and result.f1 == pytest.approx(0.75)
    promote_run.assert_called_once_with(promote_run.call_args.args[0], "p.runs", "p.prod",
                                        "new", TEST_IDS)  # fmt: skip


def test_run_is_promoted_only_if_its_f1_beats_the_champion() -> None:
    runs = {"new": _run("new"), "old": _run("old")}

    better, promote_run = _promote(
        runs, {"new": _evaluation(4, 1), "old": _evaluation(3, 1)}, "old"
    )
    assert better.promoted and better.champion_f1 == pytest.approx(0.75)
    promote_run.assert_called_once()

    worse, promote_run = _promote(runs, {"new": _evaluation(3, 1), "old": _evaluation(3, 1)}, "old")
    assert not worse.promoted
    promote_run.assert_not_called()


def test_force_promotes_a_run_that_is_not_better() -> None:
    runs = {"new": _run("new"), "old": _run("old")}
    evaluations = {"new": _evaluation(1, 1), "old": _evaluation(3, 1)}
    result, promote_run = _promote(runs, evaluations, "old", force=True)
    assert result.promoted
    promote_run.assert_called_once()


def test_a_dev_run_without_the_test_papers_cannot_be_promoted() -> None:
    with pytest.raises(PromotionError, match="lacks 1 test papers"):
        _promote({"new": _run("new", ["W1"])}, {"new": _evaluation(1, 0)}, None)


def test_a_run_not_fully_judged_cannot_be_promoted() -> None:
    cells, tables = _evaluation(1, 0)
    with pytest.raises(PromotionError, match="isn't fully judged"):
        _promote({"new": _run("new")}, {"new": (cells, tables[:-1])}, None)

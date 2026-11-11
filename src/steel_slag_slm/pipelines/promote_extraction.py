"""Copy a benchmark run into the production tables if it beats the last promoted run."""

from collections.abc import Sequence
from dataclasses import dataclass

from google.cloud import bigquery

from steel_slag_slm.data.extraction_runs import (
    latest_promoted_run_id,
    promote_run,
    read_evaluation,
    read_run,
)
from steel_slag_slm.data.extraction_tables import SHEET_TABLES
from steel_slag_slm.evaluation.extraction_metrics import score_cells
from steel_slag_slm.exceptions import PromotionError


@dataclass(frozen=True, slots=True)
class Promotion:
    """The candidate's and the last promoted run's F1 on the test papers, and the outcome."""

    run_id: str
    f1: float | None
    champion_id: str | None
    champion_f1: float | None
    promoted: bool


def _test_f1(
    client: bigquery.Client,
    runs_dataset_id: str,
    run_id: str,
    judge: tuple[str, int],
    test_ids: Sequence[str],
) -> float | None:
    """F1 of one run over the test papers, by this judge.

    Raises:
        PromotionError: If the run is unknown, misses test papers, or isn't fully judged.
    """
    run = read_run(client, runs_dataset_id, run_id)
    if run is None:
        raise PromotionError(f"No run {run_id} in {runs_dataset_id}")
    missing = sorted(set(test_ids) - set(run.paper_ids))
    if missing:
        raise PromotionError(
            f"Run {run_id} ({run.split}) lacks {len(missing)} test papers, e.g. {missing[:3]};"
            " promote a run made with extract-data --benchmark test"
        )
    cells, tables = read_evaluation(client, runs_dataset_id, run_id, judge, test_ids)
    if len(tables) < len(test_ids) * len(SHEET_TABLES):
        raise PromotionError(
            f"Run {run_id} isn't fully judged by {judge[0]} (judge prompt v{judge[1]});"
            f" run: uv run judge-extraction --run-id {run_id}"
        )
    return score_cells(cells).f1


def run_promotion(
    client: bigquery.Client,
    runs_dataset_id: str,
    production_dataset_id: str,
    run_id: str,
    judge: tuple[str, int],
    test_ids: Sequence[str],
    force: bool = False,
) -> Promotion:
    """Promote the run if its test F1 beats the last promoted run's, or if force is set.

    Both runs are scored on today's test papers by the same judge, so a changed split or
    judge prompt never compares unlike numbers.

    Raises:
        PromotionError: If either run can't be scored on the test papers by this judge.
    """
    champion_id = latest_promoted_run_id(client, runs_dataset_id)
    if champion_id == run_id:
        raise PromotionError(f"Run {run_id} is already the promoted run")
    f1 = _test_f1(client, runs_dataset_id, run_id, judge, test_ids)
    champion_f1 = (
        _test_f1(client, runs_dataset_id, champion_id, judge, test_ids) if champion_id else None
    )
    is_better = champion_id is None or (f1 or 0.0) > (champion_f1 or 0.0)
    if is_better or force:
        promote_run(client, runs_dataset_id, production_dataset_id, run_id, test_ids)
    return Promotion(run_id, f1, champion_id, champion_f1, promoted=is_better or force)

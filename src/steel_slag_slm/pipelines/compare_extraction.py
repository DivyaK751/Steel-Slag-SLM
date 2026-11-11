"""Compare every benchmark extraction run's scores, overall and per sheet, in one table."""

from pathlib import Path
from typing import Any

from google.cloud import bigquery

from steel_slag_slm.data.extraction_runs import (
    RunHistory,
    read_evaluation,
    read_run_rows,
    read_runs,
)
from steel_slag_slm.data.extraction_tables import SHEET_TABLES
from steel_slag_slm.data.reference_workbook import read_reference_rows
from steel_slag_slm.evaluation.extraction_report import summary_rows, value_rows
from steel_slag_slm.evaluation.value_metrics import count_values

# Score columns copied from a run's summary into the comparison
_SCORE_KEYS = (
    "extracted_fill_rate",
    "reference_fill_rate",
    "row_recall",
    "precision",
    "recall",
    "f1",
    "accuracy",
    "adjudicated_precision",
)


def _about(history: RunHistory) -> dict[str, Any]:
    """The run's identity columns; times as text, since Excel can't store time zones."""
    run = history.run
    return {
        "run_id": run.run_id,
        "split": run.split,
        "prompt": f"{run.prompt_name} v{run.prompt_version}",
        "model": run.model,
        "papers": len(run.paper_ids),
        "started_at": f"{history.started_at:%Y-%m-%d %H:%M}" if history.started_at else None,
        "cost_usd": round(history.cost_usd, 2) if history.cost_usd is not None else None,
        "promoted": "yes" if history.promoted_at else "",
    }


def compare_runs(
    client: bigquery.Client,
    runs_dataset_id: str,
    judge: tuple[str, int],
    reference_workbook: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Score every run, oldest first: by its values, and as judged by this judge.

    Value scores need no judge, so every run has them; row and cell scores come from this
    judge (model, prompt version) and are empty for runs it hasn't judged. Runs on different
    splits score different papers, so compare runs of the same split.

    Returns:
        One row per run (overall scores), and one row per run and sheet.
    """
    run_rows: list[dict[str, Any]] = []
    sheet_rows: list[dict[str, Any]] = []
    for history in read_runs(client, runs_dataset_id):
        run = history.run
        reference = read_reference_rows(reference_workbook, run.paper_ids)
        extracted = read_run_rows(client, runs_dataset_id, run.run_id)
        values_overall, *values_per_sheet = value_rows(
            count_values(reference, extracted, run.paper_ids)
        )
        values = {key: value for key, value in values_overall.items() if key != "sheet"}
        values_by_sheet = {row.pop("sheet"): row for row in values_per_sheet}
        cells, tables = read_evaluation(client, runs_dataset_id, run.run_id, judge, run.paper_ids)
        about = _about(history)
        if not tables:
            run_rows.append({**about, **values, "judged": "no"})
            continue
        complete = len(tables) == len(run.paper_ids) * len(SHEET_TABLES)
        overall, *per_sheet = summary_rows(cells, tables)
        judged = "yes" if complete else "partly"
        run_rows.append(
            {**about, **values, "judged": judged, **{key: overall[key] for key in _SCORE_KEYS}}
        )
        for row in per_sheet:
            sheet_values = values_by_sheet.get(row["sheet"], {})
            sheet_rows.append({"run_id": run.run_id, "split": run.split, **row, **sheet_values})
    return run_rows, sheet_rows

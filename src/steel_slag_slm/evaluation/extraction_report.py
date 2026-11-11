"""Lay out a judged extraction run as score tables: overall, per sheet and per column."""

from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font

from steel_slag_slm.data.extraction_tables import SHEET_TABLES, data_columns
from steel_slag_slm.evaluation.extraction_metrics import (
    CellScores,
    FillScores,
    score_cells,
    score_fill,
)
from steel_slag_slm.evaluation.value_metrics import ValueCount, ValueScores, sum_counts
from steel_slag_slm.schemas import ExtractionRun, JudgedCell, JudgedTable

BOLD = Font(bold=True)
ALL = "(all)"


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


def _score_columns(scores: CellScores) -> dict[str, Any]:
    return {
        "precision": _round(scores.precision),
        "recall": _round(scores.recall),
        "f1": _round(scores.f1),
        "accuracy": _round(scores.accuracy),
        "adjudicated_precision": _round(scores.adjudicated_precision),
        **{name: getattr(scores, name) for name in ("match", "partial", "mismatch")},
        **{name: getattr(scores, name) for name in ("missed", "extra")},
        **{name: getattr(scores, name) for name in ("supported", "unsupported", "unclear")},
    }


def _fill_columns(fill: FillScores) -> dict[str, Any]:
    return {
        "reference_rows": fill.reference_rows,
        "extracted_rows": fill.extracted_rows,
        "row_recall": _round(fill.row_recall),
        "row_precision": _round(fill.row_precision),
        "reference_fill_rate": _round(fill.reference_fill_rate),
        "extracted_fill_rate": _round(fill.extracted_fill_rate),
    }


def summary_rows(
    cells: Sequence[JudgedCell], tables: Sequence[JudgedTable]
) -> list[dict[str, Any]]:
    """One row for the whole run, then one per sheet, with fill rates and cell scores.

    Cell scores cover the judged sheets; the provenance sheet has fill rates only.
    """
    rows = [
        {"sheet": ALL, **_fill_columns(score_fill(tables)), **_score_columns(score_cells(cells))}
    ]
    for sheet_table in SHEET_TABLES:
        sheet_cells = [cell for cell in cells if cell.table == sheet_table.table]
        sheet_tables = [table for table in tables if table.table == sheet_table.table]
        rows.append(
            {
                "sheet": sheet_table.table,
                **_fill_columns(score_fill(sheet_tables)),
                **_score_columns(score_cells(sheet_cells)),
            }
        )
    return rows


def value_score_columns(scores: ValueScores) -> dict[str, Any]:
    """The value-level scores of one group of values, as report columns."""
    return {
        "value_precision": _round(scores.precision),
        "value_recall": _round(scores.recall),
        "value_f1": _round(scores.f1),
        "reference_values": scores.reference,
        "extracted_values": scores.extracted,
        "matched_values": scores.matched,
    }


def value_rows(counts: Sequence[ValueCount]) -> list[dict[str, Any]]:
    """One row of value-level scores for the whole run, then one per sheet that has values."""
    rows = [{"sheet": ALL, **value_score_columns(sum_counts(counts))}]
    for sheet_table in SHEET_TABLES:
        sheet_counts = [count for count in counts if count.table == sheet_table.table]
        if sheet_counts:
            rows.append(
                {"sheet": sheet_table.table, **value_score_columns(sum_counts(sheet_counts))}
            )
    return rows


def column_rows(cells: Sequence[JudgedCell], tables: Sequence[JudgedTable]) -> list[dict[str, Any]]:
    """One row per data column of the judged sheets: fill rates on each side and scores."""
    rows = []
    for sheet_table in SHEET_TABLES:
        fill = score_fill(table for table in tables if table.table == sheet_table.table)
        for column in data_columns(sheet_table):
            column_cells = [c for c in cells if c.table == sheet_table.table and c.column == column]
            if not column_cells:
                continue
            scores = score_cells(column_cells)
            rows.append(
                {
                    "sheet": sheet_table.table,
                    "column": column,
                    "reference_fill_rate": _round(
                        scores.reference_filled / fill.reference_rows
                        if fill.reference_rows
                        else None
                    ),
                    "extracted_fill_rate": _round(
                        scores.extracted_filled / fill.extracted_rows
                        if fill.extracted_rows
                        else None
                    ),
                    **_score_columns(scores),
                }
            )
    return rows


def save_workbook(path: Path, sheets: dict[str, Sequence[dict[str, Any]]]) -> None:
    """Save each list of rows as a sheet with a bold, frozen header row of its keys."""
    workbook = Workbook()
    workbook.remove(workbook.active)  # type: ignore[arg-type]
    for title, rows in sheets.items():
        sheet = workbook.create_sheet(title)
        if not rows:
            continue
        headers = list(rows[0])
        sheet.append(headers)
        for cell in sheet[1]:
            cell.font = BOLD
        sheet.freeze_panes = "A2"
        for row in rows:
            sheet.append([row.get(header) for header in headers])
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)


def write_report(
    path: Path,
    run: ExtractionRun,
    judge: tuple[str, int],
    cells: Sequence[JudgedCell],
    tables: Sequence[JudgedTable],
    value_counts: Sequence[ValueCount],
) -> None:
    """Save the run's scores to an .xlsx: summaries, per-column scores and every judged cell."""
    about: list[dict[str, Any]] = [
        {"field": "run_id", "value": run.run_id},
        {"field": "prompt", "value": f"{run.prompt_name} v{run.prompt_version}"},
        {"field": "extraction_model", "value": run.model},
        {"field": "split", "value": run.split},
        {"field": "papers", "value": len(run.paper_ids)},
        {"field": "judge", "value": f"{judge[0]}, judge prompt v{judge[1]}"},
    ]
    save_workbook(
        path,
        {
            "Run": about,
            "Values": value_rows(value_counts),
            "Summary": summary_rows(cells, tables),
            "Values per column": [asdict(count) for count in value_counts],
            "Columns": column_rows(cells, tables),
            "Sheets per paper": [asdict(table) for table in tables],
            "Cells": [asdict(cell) for cell in cells],
        },
    )


# (key, label, width) of the score columns shown in the terminal
SCORE_COLUMNS = (
    ("extracted_fill_rate", "fill", 9),
    ("reference_fill_rate", "ref fill", 9),
    ("row_recall", "row rec", 9),
    ("precision", "prec", 9),
    ("recall", "recall", 9),
    ("f1", "F1", 9),
    ("accuracy", "data acc", 9),
    ("adjudicated_precision", "adj prec", 9),
)


def format_table(rows: Sequence[dict[str, Any]], columns: Sequence[tuple[str, str, int]]) -> str:
    """A plain terminal table of the given (key, label, width) columns; floats to 2 places."""
    lines = ["".join(label.ljust(width) for _, label, width in columns)]
    for row in rows:
        cells = []
        for key, _, width in columns:
            value = row.get(key)
            text = (
                "-" if value is None else f"{value:.2f}" if isinstance(value, float) else str(value)
            )
            cells.append(text.ljust(width))
        lines.append("".join(cells).rstrip())
    return "\n".join(lines)


# (key, label, width) of the value-level score columns shown in the terminal
VALUE_COLUMNS = (
    ("reference_values", "ref vals", 9),
    ("extracted_values", "ext vals", 9),
    ("matched_values", "matched", 9),
    ("value_precision", "prec", 9),
    ("value_recall", "recall", 9),
    ("value_f1", "F1", 9),
)


def format_summary(rows: Sequence[dict[str, Any]]) -> str:
    """The terminal table of summary_rows: fill rates, row recall and cell scores per sheet."""
    return format_table(rows, (("sheet", "sheet", 31), *SCORE_COLUMNS))


def format_value_summary(rows: Sequence[dict[str, Any]]) -> str:
    """The terminal table of value_rows: value counts and value-level scores per sheet."""
    return format_table(rows, (("sheet", "sheet", 31), *VALUE_COLUMNS))

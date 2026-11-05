"""Check a model's reading of a figure against the figure's own PDF text and axis range."""

import re
from dataclasses import dataclass

from steel_slag_slm.schemas import FigureReading, TableColumn

# A value this share of the axis span outside the first/last tick still counts as in range,
# since curves often run a little past the last labelled tick
_RANGE_SLACK = 0.05
# What prompts/read_figure.yaml tells the model to call a legend that has no printed title
UNTITLED_LEGEND = "Legend"


@dataclass(frozen=True, slots=True)
class FigureCheck:
    """What could be verified about one reading without a human."""

    has_pdf_text: bool  # False for pasted-in pictures, where labels can't be checked
    labels_checked: int
    unsupported_labels: tuple[str, ...]  # read by the model but absent from the PDF text
    values_checked: int
    values_out_of_range: int  # outside the axis range the model itself reported
    rows_malformed: int  # rows without exactly one cell per column


@dataclass(frozen=True, slots=True)
class CheckTotals:
    """Automatic checks summed over a run."""

    figures_with_pdf_text: int
    labels_checked: int
    labels_unsupported: int
    values_checked: int
    values_out_of_range: int
    rows_malformed: int


def _squash(text: str) -> str:
    """Lowercase and drop everything but letters and digits, so spacing and symbols don't matter."""
    return re.sub(r"[\W_]+", "", text.lower())


def figure_body_text(pdf_text: str, caption: str) -> str:
    """The figure's PDF text without its caption, which says nothing about the drawn labels.

    A figure whose legend and axes are part of a pasted-in picture can still carry its caption
    as text; with the caption removed it correctly counts as having no text to check against.
    """
    return pdf_text.replace(caption, " ").strip() if caption else pdf_text


def _as_number(cell: str) -> float | None:
    try:
        return float(cell.replace(",", ""))
    except ValueError:
        return None


def _well_formed_rows(reading: FigureReading) -> list[list[str]]:
    return [row for row in reading.rows if len(row) == len(reading.columns)]


def reading_labels(reading: FigureReading) -> list[str]:
    """Every printed text the model claims to have read: columns, units, categories, phases, steps.

    Each distinct category (non-numeric cell) is listed once.
    """
    labels = [
        column.label
        for column in reading.columns
        if not (column.source == "legend" and column.label == UNTITLED_LEGEND)
    ]
    labels += [column.unit for column in reading.columns if column.unit]
    categories = [
        cell for row in _well_formed_rows(reading) for cell in row if _as_number(cell) is None
    ]
    labels += list(dict.fromkeys(cell for cell in categories if cell.strip()))
    labels += reading.phases
    labels += [step.label for step in reading.steps]
    return labels


def unsupported_labels(labels: list[str], pdf_text: str) -> list[str]:
    """Labels that don't appear in the PDF text, ignoring case, spacing and punctuation."""
    squashed_text = _squash(pdf_text)
    return [label for label in labels if _squash(label) not in squashed_text]


def _in_range(value: float, column: TableColumn) -> bool:
    if column.axis_min is None or column.axis_max is None:
        return True
    low, high = sorted((column.axis_min, column.axis_max))
    slack = (high - low) * _RANGE_SLACK
    return low - slack <= value <= high + slack


def count_out_of_range(reading: FigureReading) -> tuple[int, int]:
    """Return (numeric cells checked, cells outside their column's axis range)."""
    checked = out_of_range = 0
    for row in _well_formed_rows(reading):
        for cell, column in zip(row, reading.columns, strict=True):
            value = _as_number(cell)
            if value is None or column.axis_min is None or column.axis_max is None:
                continue
            checked += 1
            out_of_range += not _in_range(value, column)
    return checked, out_of_range


def check_reading(reading: FigureReading, pdf_text: str) -> FigureCheck:
    """Run every automatic check on one reading."""
    labels = reading_labels(reading)
    has_pdf_text = bool(pdf_text.strip())
    values_checked, values_out_of_range = count_out_of_range(reading)
    return FigureCheck(
        has_pdf_text=has_pdf_text,
        labels_checked=len(labels) if has_pdf_text else 0,
        unsupported_labels=tuple(unsupported_labels(labels, pdf_text)) if has_pdf_text else (),
        values_checked=values_checked,
        values_out_of_range=values_out_of_range,
        rows_malformed=len(reading.rows) - len(_well_formed_rows(reading)),
    )


def total_checks(checks: list[FigureCheck]) -> CheckTotals:
    """Add up the automatic checks of many readings."""
    return CheckTotals(
        figures_with_pdf_text=sum(check.has_pdf_text for check in checks),
        labels_checked=sum(check.labels_checked for check in checks),
        labels_unsupported=sum(len(check.unsupported_labels) for check in checks),
        values_checked=sum(check.values_checked for check in checks),
        values_out_of_range=sum(check.values_out_of_range for check in checks),
        rows_malformed=sum(check.rows_malformed for check in checks),
    )

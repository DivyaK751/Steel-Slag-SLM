"""Check a model's reading of a figure against the figure's own PDF text and axis range."""

import re
from dataclasses import dataclass

from steel_slag_slm.schemas import FigureReading

# A value this share of the axis span outside the first/last tick still counts as in range,
# since curves often run a little past the last labelled tick
_RANGE_SLACK = 0.05


@dataclass(frozen=True, slots=True)
class FigureCheck:
    """What could be verified about one reading without a human."""

    has_pdf_text: bool  # False for pasted-in pictures, where labels can't be checked
    labels_checked: int
    unsupported_labels: tuple[str, ...]  # read by the model but absent from the PDF text
    points_checked: int
    points_out_of_range: int  # outside the axis range the model itself reported


def _squash(text: str) -> str:
    """Lowercase and drop everything but letters and digits, so spacing and symbols don't matter."""
    return re.sub(r"[\W_]+", "", text.lower())


def reading_labels(reading: FigureReading) -> list[str]:
    """Every printed label the model claims to have read: axes, units, series, phases, steps."""
    labels = [
        value
        for axis in (reading.x_axis, reading.y_axis)
        if axis is not None
        for value in (axis.label, axis.unit)
        if value
    ]
    labels += [series.name for series in reading.series if series.name]
    labels += [
        point.x_category for series in reading.series for point in series.points if point.x_category
    ]
    labels += reading.phases
    labels += [step.label for step in reading.steps]
    return labels


def unsupported_labels(labels: list[str], pdf_text: str) -> list[str]:
    """Labels that don't appear in the PDF text, ignoring case, spacing and punctuation."""
    squashed_text = _squash(pdf_text)
    return [label for label in labels if _squash(label) not in squashed_text]


def _in_range(value: float, low: float | None, high: float | None) -> bool:
    if low is None or high is None:
        return True
    low, high = min(low, high), max(low, high)
    slack = (high - low) * _RANGE_SLACK
    return low - slack <= value <= high + slack


def count_out_of_range(reading: FigureReading) -> tuple[int, int]:
    """Return (points checked, points outside the reported axis ranges) for a plot."""
    x_axis, y_axis = reading.x_axis, reading.y_axis
    checked = out_of_range = 0
    for series in reading.series:
        for point in series.points:
            checked += 1
            x_ok = (
                point.x is None
                or x_axis is None
                or _in_range(point.x, x_axis.min_value, x_axis.max_value)
            )
            y_ok = y_axis is None or _in_range(point.y, y_axis.min_value, y_axis.max_value)
            out_of_range += not (x_ok and y_ok)
    return checked, out_of_range


def check_reading(reading: FigureReading, pdf_text: str) -> FigureCheck:
    """Run every automatic check on one reading."""
    labels = reading_labels(reading)
    has_pdf_text = bool(pdf_text.strip())
    points_checked, points_out_of_range = count_out_of_range(reading)
    return FigureCheck(
        has_pdf_text=has_pdf_text,
        labels_checked=len(labels) if has_pdf_text else 0,
        unsupported_labels=tuple(unsupported_labels(labels, pdf_text)) if has_pdf_text else (),
        points_checked=points_checked,
        points_out_of_range=points_out_of_range,
    )


@dataclass(frozen=True, slots=True)
class CheckTotals:
    """Automatic checks summed over a run."""

    figures_with_pdf_text: int
    labels_checked: int
    labels_unsupported: int
    points_checked: int
    points_out_of_range: int


def total_checks(checks: list[FigureCheck]) -> CheckTotals:
    """Add up the automatic checks of many readings."""
    return CheckTotals(
        figures_with_pdf_text=sum(check.has_pdf_text for check in checks),
        labels_checked=sum(check.labels_checked for check in checks),
        labels_unsupported=sum(len(check.unsupported_labels) for check in checks),
        points_checked=sum(check.points_checked for check in checks),
        points_out_of_range=sum(check.points_out_of_range for check in checks),
    )

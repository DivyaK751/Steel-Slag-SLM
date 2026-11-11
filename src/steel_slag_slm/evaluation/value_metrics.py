"""Score an extraction by its values, whatever rows they sit in: are the reference's numbers there?

Row scores (extraction_metrics) need the judge to pair rows, and a row split differently on
the two sides counts as both missed and extra. Value scores skip rows: for each paper, sheet
and value column, the reference's values and the extraction's values are matched one to one,
numbers within a tolerance. They need no model, so they compare runs whatever judge scored them.
"""

import math
import re
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass

from steel_slag_slm.data.extraction_tables import (
    ALLOWED_VALUES,
    KEY_HEADERS,
    LIST_COLUMNS,
    PROVENANCE_TABLE,
    SHEET_TABLES,
    SheetTable,
    column_unit,
    data_columns,
    is_filled,
    split_list,
)
from steel_slag_slm.data.reference_workbook import SheetRows

# Values read off figures differ a little between readers
RELATIVE_TOLERANCE = 0.05
ABSOLUTE_TOLERANCE = 0.01
# Short condition columns without a unit suffix whose values are a number or a label
CONDITION_COLUMNS = (
    "Publication_Year", "Publication_Type", "Full_Text_Access", "Relevant_To_SCM",
    "Residence_Time", "Grinding_Time", "Activator_Dosage", "Activator_Concentration",
    "Activation_Time", "Liquid_to_Solid_Ratio", "Test_Category", "Test_Age",
    "CO2_Concentration", "CO2_Pressure", "Carbonation_Time",
)  # fmt: skip
_LEADING_NUMBER = re.compile(r"^[<>≤≥~≈]?\s*([-−]?\d+(?:\.\d+)?)")


@dataclass(frozen=True, slots=True)
class ValueCount:
    """How many values one paper's column holds on each side, and how many of them match."""

    source_id: str
    table: str
    column: str
    reference: int
    extracted: int
    matched: int


@dataclass(frozen=True, slots=True)
class ValueScores:
    """Value counts summed over papers, sheets or columns, and the scores they give."""

    reference: int
    extracted: int
    matched: int

    @property
    def precision(self) -> float | None:
        """Share of the extracted values found in the reference."""
        return self.matched / self.extracted if self.extracted else None

    @property
    def recall(self) -> float | None:
        """Share of the reference's values found in the extraction."""
        return self.matched / self.reference if self.reference else None

    @property
    def f1(self) -> float | None:
        precision, recall = self.precision, self.recall
        if not precision or not recall:
            return 0.0 if precision is not None and recall is not None else None
        return 2 * precision * recall / (precision + recall)


def value_columns(sheet_table: SheetTable) -> tuple[str, ...]:
    """The sheet's columns that hold a number or a label, not free text or an ID."""
    return tuple(
        column
        for column in data_columns(sheet_table)
        if column not in KEY_HEADERS
        and (column_unit(column) or column in ALLOWED_VALUES or column in CONDITION_COLUMNS)
    )


def leading_number(value: str) -> float | None:
    """The number a value starts with ("38.2 ± 1.1" -> 38.2, ">40" -> 40), or None."""
    match = _LEADING_NUMBER.match(value.strip())
    return float(match.group(1).replace("−", "-")) if match else None


def _distance(reference: str, extracted: str) -> float | None:
    """How far apart two values are (0 is identical), or None if they don't match."""
    first, second = leading_number(reference), leading_number(extracted)
    if first is not None and second is not None:
        close = math.isclose(first, second, rel_tol=RELATIVE_TOLERANCE, abs_tol=ABSOLUTE_TOLERANCE)
        return abs(first - second) / max(abs(first), abs(second), 1e-9) if close else None
    same = " ".join(reference.split()).casefold() == " ".join(extracted.split()).casefold()
    return 0.0 if same else None


def match_values(reference: Sequence[str], extracted: Sequence[str]) -> int:
    """Pair each value with at most one on the other side, closest pairs first; count pairs."""
    pairs = sorted(
        (distance, i, j)
        for i, first in enumerate(reference)
        for j, second in enumerate(extracted)
        if (distance := _distance(first, second)) is not None
    )
    used_reference: set[int] = set()
    used_extracted: set[int] = set()
    for _, i, j in pairs:
        if i not in used_reference and j not in used_extracted:
            used_reference.add(i)
            used_extracted.add(j)
    return len(used_reference)


def _filled(rows: Iterable[dict[str, str | None]], column: str) -> list[str]:
    """The column's filled values; a list cell counts each of its values."""
    values = [str(row[column]) for row in rows if is_filled(row.get(column))]
    if column in LIST_COLUMNS:
        return [item for value in values for item in split_list(value)]
    return values


def count_values(
    reference: SheetRows, extracted: SheetRows, paper_ids: Collection[str]
) -> list[ValueCount]:
    """Count and match the values of every paper's value columns, sheet by sheet.

    A paper the extraction lacks counts all its reference values as missed.
    """
    counts = []
    for paper_id in paper_ids:
        for sheet_table in SHEET_TABLES:
            if sheet_table.table == PROVENANCE_TABLE:
                continue
            key = (paper_id, sheet_table.table)
            for column in value_columns(sheet_table):
                ours = _filled(reference.get(key, []), column)
                theirs = _filled(extracted.get(key, []), column)
                if ours or theirs:
                    matched = match_values(ours, theirs)
                    counts.append(
                        ValueCount(
                            paper_id, sheet_table.table, column, len(ours), len(theirs), matched
                        )  # fmt: skip
                    )
    return counts


def sum_counts(counts: Iterable[ValueCount]) -> ValueScores:
    """Add up value counts into one set of scores."""
    totals = [0, 0, 0]
    for count in counts:
        totals[0] += count.reference
        totals[1] += count.extracted
        totals[2] += count.matched
    return ValueScores(*totals)

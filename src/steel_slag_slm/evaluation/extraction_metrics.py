"""Score extracted sheet rows against the hand-extracted reference rows, cell by cell.

The judge pairs rows and rules on cells where both sides hold different-looking values;
everything else (empty sides, identical values, counting) is decided here, in code.
"""

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from steel_slag_slm.data.extraction_tables import (
    PROVENANCE_TABLE,
    SHEET_TABLES,
    SheetTable,
    column_unit,
    data_columns,
    is_filled,
)
from steel_slag_slm.evaluation.value_metrics import leading_number
from steel_slag_slm.schemas import (
    CellOutcome,
    CellVerdictLabel,
    JudgedCell,
    JudgedTable,
    TableJudgement,
)

# Provenance rows restate other sheets' values; they get fill rates but no cell scores
JUDGED_TABLES = tuple(table for table in SHEET_TABLES if table.table != PROVENANCE_TABLE)
# A value the judge calls partly right (e.g. right number, unit dropped) earns half a cell
PARTIAL_CREDIT = 0.5
# Numbers in a unit column this close agree: two readings of the same plot differ slightly
NUMBER_TOLERANCE = 0.02
_NUMBER = re.compile(r"^[-−]?\d+(?:\.\d+)?$")

Row = Mapping[str, str | None]
Verdicts = dict[str, tuple[CellVerdictLabel, str]]  # column -> (verdict, reason)


def numbers_agree(column: str, first: str, second: str) -> bool:
    """Whether two values of a unit column are numbers within NUMBER_TOLERANCE of each other."""
    if not column_unit(column):
        return False
    a, b = leading_number(first), leading_number(second)
    return a is not None and b is not None and math.isclose(a, b, rel_tol=NUMBER_TOLERANCE)


def is_notes_column(column: str) -> bool:
    """Whether a column holds free-form notes, which data accuracy leaves out."""
    return column.endswith("Notes")


def same_value(first: str, second: str) -> bool:
    """Whether two values are identical up to case, spacing and trailing zeros (0.30 = 0.3)."""
    a, b = (" ".join(value.split()).casefold() for value in (first, second))
    if _NUMBER.match(a) and _NUMBER.match(b):
        return float(a.replace("−", "-")) == float(b.replace("−", "-"))
    return a == b


def count_filled(rows: Iterable[Row], columns: Sequence[str]) -> int:
    """Count the data cells holding a value across rows."""
    return sum(is_filled(row.get(column)) for row in rows for column in columns)


def _valid_pairs(
    judgement: TableJudgement | None, reference_count: int, extracted_count: int
) -> list[tuple[int, int, Verdicts]]:
    """The judge's pairs that name real rows, each row used once; 1-based row numbers."""
    pairs: list[tuple[int, int, Verdicts]] = []
    used_reference: set[int] = set()
    used_extracted: set[int] = set()
    for pair in judgement.pairs if judgement else []:
        reference, extracted = pair.reference_row, pair.extracted_row
        # The judge sometimes reuses a row; the first pairing stands
        if not (1 <= reference <= reference_count and 1 <= extracted <= extracted_count):
            continue
        if reference in used_reference or extracted in used_extracted:
            continue
        used_reference.add(reference)
        used_extracted.add(extracted)
        verdicts: Verdicts = {cell.column: (cell.verdict, cell.reason) for cell in pair.cells}
        pairs.append((reference, extracted, verdicts))
    return pairs


def _pair_cells(
    source_id: str,
    sheet_table: SheetTable,
    reference: tuple[int, Row] | None,
    extracted: tuple[int, Row] | None,
    verdicts: Verdicts,
) -> list[JudgedCell]:
    """Compare every data column of a row pair; an unpaired row has None on the other side."""
    reference_row, reference_values = reference or (None, {})
    extracted_row, extracted_values = extracted or (None, {})
    cells = []
    for column in data_columns(sheet_table):
        reference_value = reference_values.get(column)
        extracted_value = extracted_values.get(column)
        has_reference, has_extracted = is_filled(reference_value), is_filled(extracted_value)
        if not has_reference and not has_extracted:
            continue
        outcome: CellOutcome
        if not has_extracted:
            outcome, reason = "missed", ""
        elif not has_reference:
            outcome, reason = "extra", ""
        elif same_value(str(reference_value), str(extracted_value)):
            outcome, reason = "match", "same value"
        elif numbers_agree(column, str(reference_value), str(extracted_value)):
            outcome, reason = "match", f"within {NUMBER_TOLERANCE:.0%}"
        else:
            outcome, reason = verdicts.get(column, ("mismatch", "judge gave no verdict"))
        cells.append(
            JudgedCell(
                source_id=source_id,
                table=sheet_table.table,
                column=column,
                reference_row=reference_row,
                extracted_row=extracted_row,
                reference_value=str(reference_value) if has_reference else None,
                extracted_value=str(extracted_value) if has_extracted else None,
                outcome=outcome,
                reason=reason,
            )
        )
    return cells


def build_cells(
    source_id: str,
    sheet_table: SheetTable,
    reference_rows: Sequence[Row],
    extracted_rows: Sequence[Row],
    judgement: TableJudgement | None,
) -> tuple[list[JudgedCell], int]:
    """Turn the judge's row pairs into one outcome per non-empty cell.

    Cells of unpaired reference rows count as missed and of unpaired extracted rows as extra.

    Returns:
        The judged cells and the number of row pairs kept.
    """
    pairs = _valid_pairs(judgement, len(reference_rows), len(extracted_rows))
    cells = []
    for reference, extracted, verdicts in pairs:
        cells += _pair_cells(
            source_id,
            sheet_table,
            (reference, reference_rows[reference - 1]),
            (extracted, extracted_rows[extracted - 1]),
            verdicts,
        )
    paired_reference = {reference for reference, _, _ in pairs}
    paired_extracted = {extracted for _, extracted, _ in pairs}
    for number, row in enumerate(reference_rows, start=1):
        if number not in paired_reference:
            cells += _pair_cells(source_id, sheet_table, (number, row), None, {})
    for number, row in enumerate(extracted_rows, start=1):
        if number not in paired_extracted:
            cells += _pair_cells(source_id, sheet_table, None, (number, row), {})
    return cells, len(pairs)


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


@dataclass(frozen=True, slots=True)
class CellScores:
    """How the extracted values of a group of cells compare with the reference."""

    match: int = 0
    partial: int = 0
    mismatch: int = 0
    missed: int = 0
    extra: int = 0
    # Adjudicated extra and mismatched values, by whether the paper supports them
    supported: int = 0
    unsupported: int = 0
    unclear: int = 0
    # The match, partial and mismatch cells that are in notes columns
    notes_match: int = 0
    notes_partial: int = 0
    notes_mismatch: int = 0

    @property
    def correct(self) -> float:
        return self.match + PARTIAL_CREDIT * self.partial

    @property
    def extracted_filled(self) -> int:
        return self.match + self.partial + self.mismatch + self.extra

    @property
    def reference_filled(self) -> int:
        return self.match + self.partial + self.mismatch + self.missed

    @property
    def precision(self) -> float | None:
        """Share of extracted values that agree with the reference."""
        return _ratio(self.correct, self.extracted_filled)

    @property
    def recall(self) -> float | None:
        """Share of reference values that were extracted correctly."""
        return _ratio(self.correct, self.reference_filled)

    @property
    def f1(self) -> float | None:
        precision, recall = self.precision, self.recall
        if not precision or not recall:
            return 0.0 if precision is not None and recall is not None else None
        return 2 * precision * recall / (precision + recall)

    @property
    def accuracy(self) -> float | None:
        """Data accuracy: where both sides filled a cell, the share that agree. Notes columns
        are left out, since two right answers can note different details."""
        agreed = (
            self.match - self.notes_match + PARTIAL_CREDIT * (self.partial - self.notes_partial)
        )
        both = self.match + self.partial + self.mismatch
        return _ratio(agreed, both - self.notes_match - self.notes_partial - self.notes_mismatch)

    @property
    def adjudicated_precision(self) -> float | None:
        """Precision once extra or mismatched values the paper supports count as right."""
        if not self.supported + self.unsupported + self.unclear:
            return None
        return _ratio(self.correct + self.supported, self.extracted_filled)


def _outcome(cell: JudgedCell) -> CellOutcome:
    """The cell's outcome, counting numbers within tolerance as a match (cells judged before
    the tolerance existed are scored the same way as new ones)."""
    if cell.outcome in ("partial", "mismatch") and numbers_agree(
        cell.column, str(cell.reference_value), str(cell.extracted_value)
    ):
        return "match"
    return cell.outcome


def score_cells(cells: Iterable[JudgedCell]) -> CellScores:
    """Count outcomes and adjudications over a group of cells."""
    counts = dict.fromkeys(("match", "partial", "mismatch", "missed", "extra"), 0)
    notes = dict.fromkeys(("notes_match", "notes_partial", "notes_mismatch"), 0)
    adjudicated = dict.fromkeys(("supported", "unsupported", "unclear"), 0)
    for cell in cells:
        outcome = _outcome(cell)
        counts[outcome] += 1
        if is_notes_column(cell.column) and f"notes_{outcome}" in notes:
            notes[f"notes_{outcome}"] += 1
        if cell.adjudication:
            adjudicated[cell.adjudication] += 1
    return CellScores(**counts, **notes, **adjudicated)


@dataclass(frozen=True, slots=True)
class FillScores:
    """Rows and filled cells on each side, against every data column of every row."""

    reference_rows: int
    extracted_rows: int
    matched_rows: int  # over the judged sheets only
    judged_reference_rows: int
    judged_extracted_rows: int
    reference_filled: int
    extracted_filled: int
    reference_capacity: int  # rows x data columns
    extracted_capacity: int

    @property
    def reference_fill_rate(self) -> float | None:
        return _ratio(self.reference_filled, self.reference_capacity)

    @property
    def extracted_fill_rate(self) -> float | None:
        return _ratio(self.extracted_filled, self.extracted_capacity)

    @property
    def row_recall(self) -> float | None:
        """Share of reference rows paired with an extracted row."""
        return _ratio(self.matched_rows, self.judged_reference_rows)

    @property
    def row_precision(self) -> float | None:
        """Share of extracted rows paired with a reference row."""
        return _ratio(self.matched_rows, self.judged_extracted_rows)


_COLUMN_COUNTS = {table.table: len(data_columns(table)) for table in SHEET_TABLES}


def score_fill(tables: Iterable[JudgedTable]) -> FillScores:
    """Add up rows and filled cells over a group of paper sheets."""
    tables = list(tables)
    judged = [table for table in tables if table.matched_rows is not None]
    return FillScores(
        reference_rows=sum(table.reference_rows for table in tables),
        extracted_rows=sum(table.extracted_rows for table in tables),
        matched_rows=sum(table.matched_rows or 0 for table in judged),
        judged_reference_rows=sum(table.reference_rows for table in judged),
        judged_extracted_rows=sum(table.extracted_rows for table in judged),
        reference_filled=sum(table.reference_filled for table in tables),
        extracted_filled=sum(table.extracted_filled for table in tables),
        reference_capacity=sum(t.reference_rows * _COLUMN_COUNTS[t.table] for t in tables),
        extracted_capacity=sum(t.extracted_rows * _COLUMN_COUNTS[t.table] for t in tables),
    )

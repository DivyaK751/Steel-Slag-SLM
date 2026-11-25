"""Apply a question's column filters to the slag catalogue and to the lookup tool's rows.

Slag type and chemistry filters choose which catalogue slags to look up; filters on result
sheets (strength, durability, carbonation...) keep only the rows that meet them.
"""

from collections.abc import Sequence
from dataclasses import replace

from steel_slag_slm.agents.question_form import ColumnFilter, FormColumn
from steel_slag_slm.data.extraction_tables import to_column_name
from steel_slag_slm.data.slag_catalogue import CATALOGUE_OXIDES, SlagCatalogueEntry
from steel_slag_slm.data.slag_chemistry import parse_value_range
from steel_slag_slm.rag.slag_lookup import (
    RESULT_SHEETS,
    SheetRow,
    SlagEvidence,
    SlagLookup,
    summarise_columns,
)

_CHEMISTRY_SHEET = "2 Feedstock Chemistry"
_SLAG_TYPE = "Steel_Slag_Type"


def value_matches(value: str | float | None, condition: ColumnFilter) -> bool:
    """Whether a reported value meets the condition; a missing value never does.

    Numbers compare as ranges, so "32-48" is above 30 and "= 40" holds for "38-42". Text
    compares without case: "=" for the whole value, "contains" for part of it.
    """
    if value is None:
        return False
    text = str(value)
    if condition.op == "contains" or isinstance(condition.value, str):
        wanted = str(condition.value).casefold()
        found = text.casefold()
        return wanted in found if condition.op == "contains" else found == wanted
    reported = parse_value_range(text)
    if reported is None:
        return False
    low, high = reported
    if isinstance(condition.value, list):
        if condition.op != "between" or len(condition.value) != 2:
            return False
        return high >= min(condition.value) and low <= max(condition.value)
    target = condition.value
    if condition.op == ">":
        return high > target
    if condition.op == "<":
        return low < target
    return low <= target <= high  # "=" (and "between" given one number)


def _header_of(condition: ColumnFilter, columns: Sequence[FormColumn]) -> FormColumn | None:
    return next((column for column in columns if column.name == condition.column), None)


def is_slag_level(column: FormColumn) -> bool:
    """Slag type and chemistry describe the slag itself, so they choose catalogue slags."""
    return column.header == _SLAG_TYPE or column.sheet == _CHEMISTRY_SHEET


def select_slags(
    catalogue: Sequence[SlagCatalogueEntry],
    filters: Sequence[ColumnFilter],
    columns: Sequence[FormColumn],
) -> list[SlagCatalogueEntry]:
    """The catalogue slags that meet the slag type and chemistry filters (all if none)."""
    chosen = list(catalogue)
    for condition in filters:
        column = _header_of(condition, columns)
        if column is None or not is_slag_level(column):
            continue
        if column.header == _SLAG_TYPE:
            chosen = [e for e in chosen if value_matches(e.steel_slag_type, condition)]
        elif (oxide := to_column_name(column.header)) in CATALOGUE_OXIDES:
            chosen = [e for e in chosen if value_matches(e.oxides.get(oxide), condition)]
    return chosen


def _keep_row(
    row: SheetRow,
    conditions: Sequence[tuple[FormColumn, ColumnFilter]],
    wanted: dict[str, set[str]],
) -> bool:
    for column, condition in conditions:
        if column.sheet == row.sheet and not value_matches(
            row.values.get(column.header), condition
        ):
            return False
    if not wanted:
        return True
    return any(header in row.values for header in wanted.get(row.sheet, set()))


def filter_lookup(
    lookup: SlagLookup,
    filters: Sequence[ColumnFilter],
    properties: Sequence[str],
    columns: Sequence[FormColumn],
) -> SlagLookup:
    """Keep only the rows that meet the result-sheet filters and report an asked-for property.

    With no properties, every row that meets the filters stays. Slags left without rows are
    dropped, and the summaries are recomputed from what remains.
    """
    by_name = {column.name: column for column in columns}
    conditions = [
        (by_name[c.column], c)
        for c in filters
        if c.column in by_name and not is_slag_level(by_name[c.column])
    ]
    wanted: dict[str, set[str]] = {}
    for name in properties:
        if name in by_name:
            wanted.setdefault(by_name[name].sheet, set()).add(by_name[name].header)
    slags: list[SlagEvidence] = []
    for slag in lookup.slags:
        rows_by_sheet = {
            sheet: kept
            for sheet, rows in slag.rows_by_sheet.items()
            if (kept := [row for row in rows if _keep_row(row, conditions, wanted)])
        }
        if rows_by_sheet:
            slags.append(replace(slag, rows_by_sheet=rows_by_sheet))
    with_data = {sheet for slag in slags for sheet in slag.rows_by_sheet}
    missing = [table.sheet for table in RESULT_SHEETS if table.sheet not in with_data]
    return SlagLookup(slags, summarise_columns(slags), missing)

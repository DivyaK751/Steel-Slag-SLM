"""The form a user's question is read into, and the extraction-table columns it may name.

The columns come from the sheet definitions in data/extraction_tables.py, so a column added
there can be asked about without changing this module.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal

from pydantic import BaseModel, Field

from steel_slag_slm.data.extraction_tables import (
    ALLOWED_VALUES,
    SHEET_TABLES,
    SheetTable,
    column_unit,
    data_columns,
    to_column_name,
)
from steel_slag_slm.data.slag_catalogue import CATALOGUE_OXIDES

MessageType = Literal["question", "chat", "off_topic", "follow_up"]
FilterOp = Literal["=", "<", ">", "between", "contains"]

# Sheets whose columns a question can ask about; sheet 9 only records where values were found
_ASKABLE_SHEETS = SHEET_TABLES[:-1]


@dataclass(frozen=True, slots=True)
class FormColumn:
    """One extraction-table column a question can ask for or filter on."""

    name: str  # "<sheet>.<column>", e.g. "concrete_performance.compressive_strength_28d_mpa"
    sheet: str  # workbook tab, e.g. "6 Concrete Performance"
    header: str  # workbook column header, e.g. "Compressive_Strength_28d_MPa"
    unit: str | None  # None for a text column
    allowed_values: tuple[str, ...]  # the controlled vocabulary, if the column has one
    seen_values: tuple[str, ...] = ()  # values the tables hold now, for short text columns

    @property
    def known_values(self) -> tuple[str, ...]:
        """The values a filter on this column can match: the vocabulary, else those seen."""
        return self.allowed_values or self.seen_values


class ColumnFilter(BaseModel):
    """One condition on a column, e.g. 28-day strength above 40 or slag type BOF."""

    column: str
    op: FilterOp
    value: float | str | list[float]


class QuestionForm(BaseModel):
    """What a message asks, in terms the tools understand. Every field may stay empty."""

    message_type: MessageType
    wants_explanation: bool = False  # asks why or how, or for a summary or recommendation
    properties: list[str] = Field(default_factory=list)  # columns whose values are wanted
    filters: list[ColumnFilter] = Field(default_factory=list)
    # Oxide wt% typed into the question, by catalogue column; the stored profile fills the rest
    chemistry: dict[str, float] = Field(default_factory=dict)
    about_my_slag: bool = False  # the question is about the user's own (profiled) slag
    missing_fields: list[str] = Field(default_factory=list)  # details that would sharpen it
    search_query: str = ""  # the question as one stand-alone search query


def sheet_alias(sheet_table: SheetTable) -> str:
    """The short sheet name used in column names, e.g. sheet_06_concrete_performance ->
    concrete_performance."""
    return sheet_table.table.split("_", 2)[2]


def form_columns(sheet_tables: tuple[SheetTable, ...] = _ASKABLE_SHEETS) -> list[FormColumn]:
    """Every data column of the given sheets, in workbook order."""
    return [
        FormColumn(
            name=f"{sheet_alias(table)}.{to_column_name(header)}",
            sheet=table.sheet,
            header=header,
            unit=column_unit(header),
            allowed_values=ALLOWED_VALUES.get(header, ()),
        )
        for table in sheet_tables
        for header in data_columns(table)
    ]


def add_seen_values(
    columns: list[FormColumn], seen: Mapping[tuple[str, str], tuple[str, ...]]
) -> list[FormColumn]:
    """The columns with the values the tables hold, keyed by (sheet, header)."""
    return [
        replace(column, seen_values=seen.get((column.sheet, column.header), ()))
        for column in columns
    ]


def _fix_filter(condition: ColumnFilter, column: FormColumn) -> ColumnFilter:
    """Spell a text filter's value as the data does; if no value matches, look for it inside
    the values instead ("OPC" finds "OPC (Type I)")."""
    if column.unit is not None or not isinstance(condition.value, str) or condition.op != "=":
        return condition
    wanted = condition.value.casefold()
    match = next((v for v in column.known_values if v.casefold() == wanted), None)
    if match is not None:
        return condition.model_copy(update={"value": match})
    return condition.model_copy(update={"op": "contains"})


def form_json_schema(columns: list[FormColumn]) -> dict[str, Any]:
    """The form's JSON schema, with column and oxide names limited to the real ones.

    Passed to the reader model as its output format, so it cannot invent a column.
    """
    schema = QuestionForm.model_json_schema()
    names = [column.name for column in columns]
    schema["properties"]["properties"]["items"] = {"type": "string", "enum": names}
    schema["$defs"]["ColumnFilter"]["properties"]["column"] = {"type": "string", "enum": names}
    schema["properties"]["chemistry"]["propertyNames"] = {"enum": list(CATALOGUE_OXIDES)}
    return schema


def clean_form(form: QuestionForm, columns: list[FormColumn]) -> QuestionForm:
    """Drop any column, filter or oxide the tables do not have, and repeated columns; spell
    text filter values as the data does.

    The reader's output is forced to the schema, but a model without forced output, or a
    hand-written form, might still name one. The schema does not limit filter values.
    """
    by_name = {column.name: column for column in columns}
    return form.model_copy(
        update={
            "properties": [p for p in dict.fromkeys(form.properties) if p in by_name],
            "filters": [
                _fix_filter(f, by_name[f.column]) for f in form.filters if f.column in by_name
            ],
            "chemistry": {k: v for k, v in form.chemistry.items() if k in CATALOGUE_OXIDES},
        }
    )

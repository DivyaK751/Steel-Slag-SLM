"""The form a user's question is read into, and the extraction-table columns it may name.

The columns come from the sheet definitions in data/extraction_tables.py, so a column added
there can be asked about without changing this module.
"""

from dataclasses import dataclass
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
Intent = Literal["value_lookup", "similar_slags", "explanation"]
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


class ColumnFilter(BaseModel):
    """One condition on a column, e.g. 28-day strength above 40 or slag type BOF."""

    column: str
    op: FilterOp
    value: float | str | list[float]


class QuestionForm(BaseModel):
    """What a message asks, in terms the tools understand. Every field may stay empty."""

    message_type: MessageType
    intents: list[Intent] = Field(default_factory=list)
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
    """Drop any column, filter or oxide the tables do not have, and repeated entries.

    The reader's output is forced to the schema, but a model without forced output, or a
    hand-written form, might still name one.
    """
    known = {column.name for column in columns}
    return form.model_copy(
        update={
            "intents": list(dict.fromkeys(form.intents)),
            "properties": [p for p in dict.fromkeys(form.properties) if p in known],
            "filters": [f for f in form.filters if f.column in known],
            "chemistry": {k: v for k, v in form.chemistry.items() if k in CATALOGUE_OXIDES},
        }
    )

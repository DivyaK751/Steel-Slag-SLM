import json

from steel_slag_slm.agents.extraction_judge import format_claims, format_columns, format_rows
from steel_slag_slm.data.extraction_tables import TABLES_BY_NAME
from steel_slag_slm.llm.prompts import load_prompt
from steel_slag_slm.schemas import JudgedCell

CHEMISTRY = TABLES_BY_NAME["sheet_02_feedstock_chemistry"]


def test_format_rows_numbers_rows_and_shows_only_filled_cells() -> None:
    rows = [
        {"Source_ID": "W1", "Material_ID": "MAT-0001", "CaO_pct": "40", "MgO_pct": "NA"},
        {"Source_ID": "W1", "Material_ID": "MAT-0002", "CaO_pct": None},
    ]

    lines = format_rows("R", rows, CHEMISTRY).splitlines()

    assert lines[0] == 'R1: {"Material_ID": "MAT-0001", "CaO_pct": "40"}'
    assert lines[1] == 'R2: {"Material_ID": "MAT-0002"}'


def test_format_rows_marks_an_empty_side() -> None:
    assert format_rows("E", [], CHEMISTRY) == "(none)"


def test_format_columns_lists_data_columns_with_units_but_not_ids() -> None:
    columns = format_columns(CHEMISTRY)
    assert "- CaO_pct [%]" in columns
    assert "- Analysis_Method\n" in columns
    assert "Material_ID" not in columns


def test_format_claims_gives_each_disputed_value_its_row() -> None:
    cell = JudgedCell("W1", CHEMISTRY.table, "CaO_pct", None, 1, None, "40", "extra")
    row = {"Source_ID": "W1", "Material_ID": "MAT-0001", "CaO_pct": "40", "MgO_pct": None}

    claim = json.loads(format_claims([(cell, row)], {CHEMISTRY.table: CHEMISTRY.sheet}))

    assert claim == {
        "claim_id": 1,
        "sheet": "2 Feedstock Chemistry",
        "column": "CaO_pct",
        "system_value": "40",
        "key_value": None,
        "row": {"Material_ID": "MAT-0001", "CaO_pct": "40"},
    }


def test_judge_and_adjudication_prompts_render_their_inputs() -> None:
    judge = load_prompt("judge_extraction")
    message = judge.render_input(
        sheet="S", columns="C", reference_rows="R1: {}", extracted_rows="E"
    )
    assert "R1: {}" in message

    adjudicate = load_prompt("adjudicate_extraction")
    message = adjudicate.render_input(openalex_id="W1", claims="{}", markdown="| a | {b} |")
    assert "| a | {b} |" in message

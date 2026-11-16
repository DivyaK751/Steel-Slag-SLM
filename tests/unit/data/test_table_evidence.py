import pytest
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.table.table_data import TableCell, TableData

from steel_slag_slm.data.table_evidence import TableEvidence, render_table_evidence
from steel_slag_slm.data.table_inspection import InspectedTable, capture_tables


def _cell(row: int, col: int, text: str, **kwargs) -> TableCell:
    return TableCell(
        start_row_offset_idx=row,
        end_row_offset_idx=row + kwargs.get("row_span", 1),
        start_col_offset_idx=col,
        end_col_offset_idx=col + kwargs.get("col_span", 1),
        text=text,
        **kwargs,
    )


def _table() -> InspectedTable:
    document = DoclingDocument(name="test")
    document.add_table(
        data=TableData(
            num_rows=4,
            num_cols=3,
            table_cells=[
                _cell(0, 0, "Table 11: 35% SS", col_span=3, column_header=True),
                _cell(1, 0, "Mix", row_span=2, column_header=True),
                _cell(1, 1, "Strength (MPa)", col_span=2, column_header=True),
                _cell(2, 1, "3 d", column_header=True),
                _cell(2, 2, "28 d", column_header=True),
                _cell(3, 0, "9#"),
                _cell(3, 1, "-"),
                _cell(3, 2, ">45.14"),
            ],
        )
    )
    return capture_tables(document)[0]


def test_nested_headers_preserve_values_and_context_once_without_mutation() -> None:
    table = _table()
    before = table.model_dump_json()
    rendered = render_table_evidence(table)
    assert rendered.review_status == "unreviewed" and not rendered.issues
    assert rendered.rows[0].text == (
        "Table 11: 35% SS\nMix: 9#\nStrength (MPa) > 3 d: -\nStrength (MPa) > 28 d: >45.14"
    )
    assert rendered.rows[0].cell_indices == [5, 6, 7]
    assert rendered.rows[0].row_index == 3
    assert TableEvidence.model_validate_json(rendered.model_dump_json()) == rendered
    assert table.model_dump_json() == before


def test_only_explicit_body_row_spans_are_expanded() -> None:
    table = _table()
    table.data.num_rows = 5
    table.data.table_cells[5].row_span = 2
    table.data.table_cells[5].end_row_offset_idx = 5
    table.data.table_cells.extend([_cell(4, 1, "6.45"), _cell(4, 2, "45.14")])
    assert "Mix: 9#" in render_table_evidence(table).rows[1].text
    table.data.table_cells[5].row_span = 1
    table.data.table_cells[5].end_row_offset_idx = 4
    rendered = render_table_evidence(table)
    assert len(rendered.rows) == 1
    assert rendered.issues[0].row_index == 4


@pytest.mark.parametrize("problem", ["no_context", "no_header", "body_span", "section", "empty"])
def test_ambiguous_inputs_are_reported_not_guessed(problem: str) -> None:
    table = _table()
    if problem == "no_context":
        table.data.table_cells[0].text = ""
    elif problem == "no_header":
        table.data.table_cells[2].text = ""
        table.data.table_cells[4].text = ""
    elif problem == "body_span":
        table.data.table_cells[-2].col_span = 2
        table.data.table_cells[-2].end_col_offset_idx = 3
        table.data.table_cells.pop()
    elif problem == "section":
        table.data.table_cells[-1].row_section = True
    else:
        table.data.table_cells[-1].text = ""
    rendered = render_table_evidence(table)
    assert not rendered.rows and rendered.issues


@pytest.mark.parametrize("problem", ["overlap", "bounds", "span"])
def test_invalid_geometry_fails_explicitly(problem: str) -> None:
    table = _table()
    if problem == "overlap":
        table.data.table_cells.append(table.data.table_cells[-1].model_copy())
    elif problem == "bounds":
        table.data.table_cells[-1].end_col_offset_idx = 4
    else:
        table.data.table_cells[-1].row_span = 2
    with pytest.raises(ValueError, match="invalid geometry|overlapping cells"):
        render_table_evidence(table)


def test_units_factor_codes_and_footnotes_are_not_reinterpreted() -> None:
    table = _table()
    table.caption = "Table 4"
    table.footnotes = ["As reported"]
    table.data.table_cells[2].text = "Heavy metals (mg/kg)"
    table.data.table_cells[4].text = "Cr"
    table.data.table_cells[-1].text = "442.30"
    table.data.table_cells[5].text = "A(1)"
    row = render_table_evidence(table).rows[0]
    assert "Heavy metals (mg/kg) > Cr: 442.30" in row.text
    assert "Mix: A(1)" in row.text and row.text.endswith("Footnotes: As reported")


def test_crossing_header_groups_are_not_rendered_as_nested_paths() -> None:
    table = _table()
    table.data.table_cells[1] = _cell(1, 0, "Group A", column_header=True)
    table.data.table_cells[2] = _cell(1, 1, "Group B", col_span=2, column_header=True)
    table.data.table_cells[3] = _cell(2, 0, "Crossing group", col_span=2, column_header=True)
    rendered = render_table_evidence(table)
    assert not rendered.rows and rendered.issues[0].reason == "Non-nested column headers"


def test_missing_headers_and_empty_tables_are_explicit() -> None:
    table = _table()
    for cell in table.data.table_cells:
        cell.column_header = False
    assert render_table_evidence(table).issues[0].reason == "Missing or interleaved column headers"
    table.data.table_cells.clear()
    assert render_table_evidence(table).issues

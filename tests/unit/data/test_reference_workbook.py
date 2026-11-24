from pathlib import Path

import pytest
from openpyxl import Workbook

from steel_slag_slm.data.extraction_tables import SHEET_TABLES
from steel_slag_slm.data.reference_workbook import read_reference_rows


def _write_workbook(path: Path, chemistry_rows: list[list[object]]) -> None:
    workbook = Workbook()
    workbook.remove(workbook.active)  # type: ignore[arg-type]
    for sheet_table in SHEET_TABLES:
        sheet = workbook.create_sheet(sheet_table.sheet)
        sheet.append(list(sheet_table.headers))
        if sheet_table.table == "sheet_02_feedstock_chemistry":
            for row in chemistry_rows:
                sheet.append(row + [None] * (len(sheet_table.headers) - len(row)))
    workbook.save(path)


def test_read_reference_rows_keeps_requested_papers_with_bare_ids(tmp_path: Path) -> None:
    path = tmp_path / "reference.xlsx"
    _write_workbook(
        path,
        [
            ["SRC-W1", "MAT-0001", "EXP-0001", "BOF", "Plant A", None, 40.42],
            ["SRC-W2", "MAT-0001", "EXP-0001", "EAF", "Plant B", None, 30],
        ],
    )

    rows = read_reference_rows(path, {"W1"})

    assert list(rows) == [("W1", "sheet_02_feedstock_chemistry")]
    row = rows[("W1", "sheet_02_feedstock_chemistry")][0]
    assert row["CaO_pct"] == "40.42"  # numbers come back as text
    assert row["Sampling_Date_or_Year"] is None


def test_read_reference_rows_rejects_a_workbook_with_other_headers(tmp_path: Path) -> None:
    path = tmp_path / "reference.xlsx"
    workbook = Workbook()
    workbook.active.title = SHEET_TABLES[0].sheet  # type: ignore[union-attr]
    workbook.active.append(["Source_ID", "Something_Else"])  # type: ignore[union-attr]
    workbook.save(path)

    with pytest.raises(ValueError, match="other headers"):
        read_reference_rows(path, {"W1"})

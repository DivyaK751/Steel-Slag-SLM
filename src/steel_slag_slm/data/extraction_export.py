"""Export the extraction tables from BigQuery to one Excel workbook, one sheet per table."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from google.cloud import bigquery
from openpyxl import Workbook
from openpyxl.styles import Font

from steel_slag_slm.data.extraction_tables import SHEET_TABLES

BOLD = Font(bold=True)
# Rows of one paper, material and experiment sit together in each sheet
_SORT_COLUMNS = ("source_id", "material_id", "experiment_id", "mix_id")


def _excel_value(value: Any) -> Any:
    # Excel can't store time zones, so timestamps are written as UTC without one
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def export_workbook(client: bigquery.Client, dataset_id: str, output_path: Path) -> dict[str, int]:
    """Write every sheet table in project.dataset to one .xlsx file; return rows per table."""
    workbook = Workbook()
    workbook.remove(workbook.active)  # type: ignore[arg-type]
    row_counts = {}
    for sheet_table in SHEET_TABLES:
        table = client.get_table(f"{dataset_id}.{sheet_table.table}")
        columns = [field.name for field in table.schema]
        # list_rows reads table storage directly, so unlike a query it is free
        rows = sorted(
            client.list_rows(table),
            key=lambda row: tuple(str(row.get(name) or "") for name in _SORT_COLUMNS),
        )
        sheet = workbook.create_sheet(sheet_table.table)
        sheet.append(columns)
        for cell in sheet[1]:
            cell.font = BOLD
        sheet.freeze_panes = "A2"
        for row in rows:
            sheet.append([_excel_value(row.get(name)) for name in columns])
        row_counts[sheet_table.table] = len(rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)
    return row_counts

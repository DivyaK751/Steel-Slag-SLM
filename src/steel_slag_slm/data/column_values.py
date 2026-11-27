"""The values the extraction tables actually hold in their short text columns.

Shown to the question reader next to each column, so its filters use the data's own wording
("CEM I 42.5", not "OPC") and pick up values added since the code was written.
"""

from collections.abc import Sequence

from google.cloud import bigquery

from steel_slag_slm.data.bigquery_store import query_rows
from steel_slag_slm.data.extraction_tables import (
    ALLOWED_VALUES,
    SHEET_TABLES,
    SheetTable,
    column_unit,
    data_columns,
    is_filled,
    to_column_name,
)

# Columns with more distinct values than this are free text (notes, titles) and not returned
_MAX_QUERIED_VALUES = 50

SeenValues = dict[tuple[str, str], tuple[str, ...]]  # (sheet, header) -> values, most common first


def text_columns(sheet_table: SheetTable) -> list[str]:
    """Headers holding text with no fixed vocabulary: no unit, and not limited by the workbook."""
    return [
        header
        for header in data_columns(sheet_table)
        if column_unit(header) is None and header not in ALLOWED_VALUES
    ]


def _values_sql(dataset_id: str, sheet_tables: Sequence[SheetTable]) -> str:
    # Table and column names come from the sheet definitions, never from user input
    selects = [
        f"SELECT '{table.sheet}' AS sheet, '{header}' AS header, "
        f"TRIM(CAST({to_column_name(header)} AS STRING)) AS value "
        f"FROM `{dataset_id}.{table.table}`"
        for table in sheet_tables
        for header in text_columns(table)
    ]
    return (
        f"WITH vals AS ({' UNION ALL '.join(selects)}), "
        "counted AS (SELECT sheet, header, value, COUNT(*) AS n FROM vals "
        "WHERE value IS NOT NULL AND value != '' GROUP BY sheet, header, value) "
        "SELECT sheet, header, value, n FROM ("
        "SELECT *, COUNT(*) OVER (PARTITION BY sheet, header) AS kinds FROM counted) "
        "WHERE kinds <= @max_values ORDER BY sheet, header, n DESC, value"
    )


def read_seen_values(
    client: bigquery.Client,
    dataset_id: str,
    max_values: int,
    sheet_tables: Sequence[SheetTable] = SHEET_TABLES[:-1],
) -> SeenValues:
    """The values of every text column with at most max_values distinct ones, most common first.

    Blank and NA cells are not values. Columns with more values are left out: they hold free
    text, and listing them would flood the reader's prompt.
    """
    rows = query_rows(
        client, _values_sql(dataset_id, sheet_tables), {"max_values": _MAX_QUERIED_VALUES}
    )
    found: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        if is_filled(row["value"]):
            found.setdefault((row["sheet"], row["header"]), []).append(row["value"])
    return {key: tuple(values) for key, values in found.items() if len(values) <= max_values}

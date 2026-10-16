"""Read rows from and write column updates to BigQuery tables."""

import re
from collections.abc import Mapping, Sequence
from typing import Any

from google.cloud import bigquery

# Only plain identifiers are pasted into SQL; everything else goes in as a query parameter
_COLUMN_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TABLE_PATTERN = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")
_SCALAR_TYPES: dict[type, str] = {bool: "BOOL", int: "INT64", float: "FLOAT64", str: "STRING"}


def _check_identifier(name: str, pattern: re.Pattern[str]) -> str:
    if not pattern.match(name):
        raise ValueError(f"Unsafe BigQuery identifier: {name!r}")
    return name


def _to_query_parameter(
    name: str, value: Any
) -> bigquery.ScalarQueryParameter | bigquery.ArrayQueryParameter:
    """Turn a Python value (scalar or list of scalars) into a typed query parameter.

    Raises:
        TypeError: If the value's type has no BigQuery equivalent here.
    """
    if isinstance(value, list):
        # An empty list has no element to infer from; only string lists are used so far
        element_type = _SCALAR_TYPES[type(value[0])] if value else "STRING"
        return bigquery.ArrayQueryParameter(name, element_type, value)
    if type(value) not in _SCALAR_TYPES:
        raise TypeError(f"Query parameter {name} has unsupported type {type(value).__name__}")
    return bigquery.ScalarQueryParameter(name, _SCALAR_TYPES[type(value)], value)


def query_rows(
    client: bigquery.Client, sql: str, params: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Run a parameterized query (@name placeholders) and return its rows as dicts."""
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            _to_query_parameter(name, value) for name, value in (params or {}).items()
        ]
    )
    return [dict(row.items()) for row in client.query(sql, job_config=job_config).result()]


def build_merge_sql(table_id: str, key: str, columns: Sequence[str]) -> str:
    """SQL that updates the given columns of rows whose key matches an entry in @rows."""
    _check_identifier(table_id, _TABLE_PATTERN)
    for name in (key, *columns):
        _check_identifier(name, _COLUMN_PATTERN)
    assignments = ",\n    ".join(f"{column} = source.{column}" for column in columns)
    return (
        f"MERGE `{table_id}` AS target\n"
        f"USING UNNEST(@rows) AS source\n"
        f"ON target.{key} = source.{key}\n"
        f"WHEN MATCHED THEN UPDATE SET\n    {assignments}"
    )


def merge_updates(
    client: bigquery.Client,
    table_id: str,
    key: str,
    rows: Sequence[Mapping[str, Any]],
    column_types: Mapping[str, str],
) -> int:
    """Update existing rows in one MERGE job, matching on key; returns rows changed.

    column_types maps every column in the rows (key included) to its BigQuery type, e.g.
    {"openalex_id": "STRING", "relevance_score": "FLOAT64"}. Rows with no match are ignored.
    """
    if not rows:
        return 0
    # The rows travel as one ARRAY<STRUCT> parameter, so no staging table is needed
    rows_param = bigquery.ArrayQueryParameter(
        "rows",
        "STRUCT",
        [
            bigquery.StructQueryParameter(
                None,
                *(
                    bigquery.ScalarQueryParameter(column, column_type, row.get(column))
                    for column, column_type in column_types.items()
                ),
            )
            for row in rows
        ],
    )
    columns = [column for column in column_types if column != key]
    job = client.query(
        build_merge_sql(table_id, key, columns),
        job_config=bigquery.QueryJobConfig(query_parameters=[rows_param]),
    )
    job.result()
    return int(job.num_dml_affected_rows or 0)

"""Fixed, parameterised reads of the extraction tables for a handful of papers."""

from collections.abc import Sequence
from typing import Any

from google.cloud import bigquery

from steel_slag_slm.data.bigquery_store import query_rows
from steel_slag_slm.data.extraction_tables import LITERATURE_TABLE, PROVENANCE_TABLE

_FOR_PAPERS = "WHERE source_id IN UNNEST(@source_ids)"


def read_sheet_rows(
    client: bigquery.Client, dataset_id: str, table: str, source_ids: Sequence[str]
) -> list[dict[str, Any]]:
    """Every row of one extraction table that belongs to these papers."""
    sql = f"SELECT * FROM `{dataset_id}.{table}` {_FOR_PAPERS}"
    return query_rows(client, sql, {"source_ids": list(source_ids)})


def read_citations(
    client: bigquery.Client, dataset_id: str, source_ids: Sequence[str]
) -> list[dict[str, Any]]:
    """Title, authors, year and DOI of these papers, from the Scientific Literature sheet."""
    sql = (
        "SELECT source_id, title, authors, publication_year, doi_or_url"
        f" FROM `{dataset_id}.{LITERATURE_TABLE}` {_FOR_PAPERS}"
    )
    return query_rows(client, sql, {"source_ids": list(source_ids)})


def read_provenance(
    client: bigquery.Client, dataset_id: str, source_ids: Sequence[str]
) -> list[dict[str, Any]]:
    """Where in each paper every extracted value was found (page, table or figure)."""
    sql = (
        "SELECT source_id, material_id, experiment_id, fact_or_data_element,"
        " page_or_table_or_figure"
        f" FROM `{dataset_id}.{PROVENANCE_TABLE}` {_FOR_PAPERS}"
    )
    return query_rows(client, sql, {"source_ids": list(source_ids)})

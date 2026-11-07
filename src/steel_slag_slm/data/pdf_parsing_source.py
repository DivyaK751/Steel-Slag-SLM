"""Read source PDF links from a BigQuery table of screened papers."""

import re
from dataclasses import dataclass

from google.cloud import bigquery

from steel_slag_slm.data.bigquery_store import query_rows

_TABLE_PATTERN = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")


@dataclass(frozen=True, slots=True)
class PdfSource:
    """Identify a paper and the Drive link for its source PDF."""

    openalex_id: str
    drive_link: str


def list_pdf_sources(
    client: bigquery.Client, table_id: str, relevant_labels: list[str] | None = None
) -> list[PdfSource]:
    """Return screened-paper rows that have an OpenAlex ID and a nonempty Drive link.

    relevant_labels keeps only papers whose screening label is one of them (e.g. ["Yes"]);
    None keeps every paper.
    """
    if not _TABLE_PATTERN.fullmatch(table_id):
        raise ValueError(f"Invalid BigQuery table ID: {table_id!r}")

    label_filter = "AND relevant IN UNNEST(@relevant_labels)" if relevant_labels else ""

    rows = query_rows(
        client,
        f"""
        SELECT DISTINCT openalex_id, drive_link
        FROM `{table_id}`
        WHERE openalex_id IS NOT NULL
          AND TRIM(openalex_id) != ''
          AND drive_link IS NOT NULL
          AND TRIM(drive_link) != ''
          {label_filter}
        ORDER BY openalex_id
        """,
        {"relevant_labels": relevant_labels} if relevant_labels else None,
    )

    sources = []
    for row in rows:
        openalex_id = row["openalex_id"]
        drive_link = row["drive_link"]
        if not isinstance(openalex_id, str) or not isinstance(drive_link, str):
            raise ValueError("BigQuery returned a paper row with invalid PDF source fields")
        sources.append(PdfSource(openalex_id=openalex_id, drive_link=drive_link))
    return sources

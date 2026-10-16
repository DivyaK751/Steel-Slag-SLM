"""Keep one BigQuery row per paper in the shared Drive folder."""

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from google.cloud import bigquery
from google.oauth2 import service_account

from steel_slag_slm.config import RegistrySettings
from steel_slag_slm.schemas import Paper

BIGQUERY_SCOPES = ["https://www.googleapis.com/auth/bigquery"]
DRIVE_FILE_URL = "https://drive.google.com/file/d/{file_id}/view"

PAPERS_SCHEMA = [
    bigquery.SchemaField("openalex_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("title", "STRING"),
    bigquery.SchemaField("authors", "STRING", mode="REPEATED"),
    bigquery.SchemaField("year", "INT64"),
    bigquery.SchemaField("doi", "STRING"),
    bigquery.SchemaField("language", "STRING"),
    bigquery.SchemaField("abstract", "STRING"),
    bigquery.SchemaField("keywords", "STRING", mode="REPEATED"),
    bigquery.SchemaField("drive_file_id", "STRING"),
    bigquery.SchemaField("drive_link", "STRING"),
    bigquery.SchemaField("added_at", "TIMESTAMP"),
    # Filled in later by relevance screening; empty when a row is first added
    bigquery.SchemaField("relevant", "STRING", description="Yes / No / Unclear"),
    bigquery.SchemaField("relevance_score", "FLOAT64", description="0.0-1.0 model confidence"),
    bigquery.SchemaField("relevance_confidence", "STRING", description="High / Medium / Low"),
    bigquery.SchemaField("relevance_reason", "STRING", description="One-sentence explanation"),
    bigquery.SchemaField("supporting_excerpt", "STRING", description="Quote from the abstract"),
]


def build_bigquery_client(project: str, credentials_file: Path | None) -> bigquery.Client:
    """Create a BigQuery client from a service account key or, failing that, the gcloud login."""
    if credentials_file:
        credentials = service_account.Credentials.from_service_account_file(
            str(credentials_file), scopes=BIGQUERY_SCOPES
        )
        return bigquery.Client(project=project, credentials=credentials)
    return bigquery.Client(project=project)


def ensure_papers_table(client: bigquery.Client, settings: RegistrySettings) -> str:
    """Create the dataset and papers table if they don't exist, returning the table's full ID."""
    dataset = bigquery.Dataset(f"{client.project}.{settings.dataset}")
    dataset.location = settings.location
    client.create_dataset(dataset, exists_ok=True)
    table_id = f"{client.project}.{settings.dataset}.{settings.table}"
    client.create_table(bigquery.Table(table_id, schema=PAPERS_SCHEMA), exists_ok=True)
    return table_id


def list_registered_ids(client: bigquery.Client, table_id: str) -> set[str]:
    """Return the OpenAlex IDs that already have a row."""
    rows = client.query(f"SELECT openalex_id FROM `{table_id}`").result()
    return {row.openalex_id for row in rows}


def build_row(paper: Paper, drive_file_id: str, added_at: datetime) -> dict[str, Any]:
    """Turn a paper and its Drive file into one row of the papers table."""
    return {
        "openalex_id": paper.openalex_id,
        "title": paper.title or None,
        "authors": list(paper.authors),
        "year": paper.year,
        "doi": paper.doi,
        "language": paper.language,
        # Empty means OpenAlex has no abstract; NULL makes those rows easy to find later
        "abstract": paper.abstract or None,
        "keywords": list(paper.keywords),
        "drive_file_id": drive_file_id,
        "drive_link": DRIVE_FILE_URL.format(file_id=drive_file_id),
        "added_at": added_at.isoformat(),
    }


def add_rows(client: bigquery.Client, table_id: str, rows: Sequence[dict[str, Any]]) -> None:
    """Append rows to the papers table and wait until they are saved."""
    if not rows:
        return
    # A load job, not a streaming insert: streamed rows can't be updated for ~30 minutes,
    # which would block the screening step from filling in its columns
    job_config = bigquery.LoadJobConfig(schema=PAPERS_SCHEMA, write_disposition="WRITE_APPEND")
    client.load_table_from_json(list(rows), table_id, job_config=job_config).result()

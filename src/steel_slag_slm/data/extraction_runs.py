"""BigQuery storage for benchmark extraction runs: their rows, their scores, and promotion.

Each extract-data --benchmark run writes its rows, tagged with a run ID, to copies of the
sheet tables in a separate dataset, so runs of different prompt versions sit side by side.
judge-extraction stores its verdicts next to them, and promote-extraction copies the rows of
a run that scored better than the last promoted one into the production tables.
"""

from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from google.cloud import bigquery

from steel_slag_slm.config import ExtractionTablesSettings
from steel_slag_slm.data.extraction_tables import (
    SHEET_TABLES,
    build_schema,
    ensure_extraction_tables,
    to_column_name,
)
from steel_slag_slm.data.reference_workbook import SheetRows
from steel_slag_slm.schemas import ExtractionRun, JudgedCell, JudgedTable

RUNS_TABLE = "runs"
EVAL_CELLS_TABLE = "eval_cells"
EVAL_TABLES_TABLE = "eval_tables"

_STRING, _INTEGER = "STRING", "INTEGER"
RUNS_SCHEMA = [
    bigquery.SchemaField("run_id", _STRING, mode="REQUIRED"),
    bigquery.SchemaField("prompt_name", _STRING),
    bigquery.SchemaField("prompt_version", _INTEGER),
    bigquery.SchemaField("model", _STRING),
    bigquery.SchemaField("split", _STRING, description="Benchmark split: dev or test"),
    bigquery.SchemaField("paper_ids", _STRING, mode="REPEATED"),
    bigquery.SchemaField("started_at", "TIMESTAMP"),
    bigquery.SchemaField("cost_usd", "FLOAT", description="Gemini and BigQuery, list prices"),
    bigquery.SchemaField("promoted_at", "TIMESTAMP", description="When copied to production"),
]
# Every judgement row names its judge, so scores from different judges are never mixed
_JUDGE_FIELDS = [
    bigquery.SchemaField("run_id", _STRING, mode="REQUIRED"),
    bigquery.SchemaField("judge_model", _STRING, mode="REQUIRED"),
    bigquery.SchemaField("judge_prompt_version", _INTEGER, mode="REQUIRED"),
    bigquery.SchemaField("judged_at", "TIMESTAMP"),
    bigquery.SchemaField("source_id", _STRING, mode="REQUIRED"),
    bigquery.SchemaField("sheet_table", _STRING, mode="REQUIRED"),
]
EVAL_CELLS_SCHEMA = _JUDGE_FIELDS + [
    bigquery.SchemaField("column_name", _STRING, description="Workbook header"),
    bigquery.SchemaField("reference_row", _INTEGER),
    bigquery.SchemaField("extracted_row", _INTEGER),
    bigquery.SchemaField("reference_value", _STRING),
    bigquery.SchemaField("extracted_value", _STRING),
    bigquery.SchemaField("outcome", _STRING, description="match/partial/mismatch/missed/extra"),
    bigquery.SchemaField("reason", _STRING),
    bigquery.SchemaField("adjudication", _STRING, description="supported/unsupported/unclear"),
    bigquery.SchemaField("adjudication_reason", _STRING),
]
EVAL_TABLES_SCHEMA = _JUDGE_FIELDS + [
    bigquery.SchemaField(name, _INTEGER)
    for name in (
        "reference_rows",
        "extracted_rows",
        "reference_filled",
        "extracted_filled",
        "matched_rows",
    )
]
_HEADERS_BY_COLUMN = {
    table.table: {to_column_name(header): header for header in table.headers}
    for table in SHEET_TABLES
}


def ensure_run_tables(client: bigquery.Client, dataset: str, location: str) -> None:
    """Create the runs dataset: run-tagged sheet tables, the run list and the score tables."""
    settings = ExtractionTablesSettings(dataset=dataset, location=location, export_dir=Path("."))
    ensure_extraction_tables(client, settings, with_run_id=True)
    for name, schema in (
        (RUNS_TABLE, RUNS_SCHEMA),
        (EVAL_CELLS_TABLE, EVAL_CELLS_SCHEMA),
        (EVAL_TABLES_TABLE, EVAL_TABLES_SCHEMA),
    ):
        table = bigquery.Table(f"{client.project}.{dataset}.{name}", schema=schema)
        if name != RUNS_TABLE:
            table.clustering_fields = ["run_id", "source_id"]
        client.create_table(table, exists_ok=True)


def _load(
    client: bigquery.Client,
    table_id: str,
    rows: list[dict[str, Any]],
    schema: list[bigquery.SchemaField],
) -> None:
    # A load job, not a streaming insert, so the rows can be updated or deleted right away
    if rows:
        config = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_APPEND")
        client.load_table_from_json(rows, table_id, job_config=config).result()


def _params(**values: str | int | float | list[str] | None) -> bigquery.QueryJobConfig:
    """Query parameters from keyword values: lists are string arrays, numbers keep their type."""
    params: list[bigquery.ScalarQueryParameter | bigquery.ArrayQueryParameter] = []
    for name, value in values.items():
        if isinstance(value, list):
            params.append(bigquery.ArrayQueryParameter(name, _STRING, sorted(value)))
        elif isinstance(value, float):
            params.append(bigquery.ScalarQueryParameter(name, "FLOAT64", value))
        elif isinstance(value, int):
            params.append(bigquery.ScalarQueryParameter(name, _INTEGER, value))
        else:
            params.append(bigquery.ScalarQueryParameter(name, _STRING, value))
    return bigquery.QueryJobConfig(query_parameters=params)


def record_run(
    client: bigquery.Client, dataset_id: str, run: ExtractionRun, started_at: datetime
) -> None:
    """Add a run to the run list before it starts, so a crashed run still shows up."""
    row = {**asdict(run), "paper_ids": list(run.paper_ids), "started_at": started_at.isoformat()}
    _load(client, f"{dataset_id}.{RUNS_TABLE}", [row], RUNS_SCHEMA)


def set_run_cost(client: bigquery.Client, dataset_id: str, run_id: str, cost_usd: float) -> None:
    """Store what a finished run cost."""
    client.query(
        f"UPDATE `{dataset_id}.{RUNS_TABLE}` SET cost_usd = @cost WHERE run_id = @run_id",
        job_config=_params(cost=cost_usd, run_id=run_id),
    ).result()


def read_run(client: bigquery.Client, dataset_id: str, run_id: str) -> ExtractionRun | None:
    """Return a run from the run list, or None if there is no such run."""
    rows = list(
        client.query(
            f"SELECT * FROM `{dataset_id}.{RUNS_TABLE}` WHERE run_id = @run_id",
            job_config=_params(run_id=run_id),
        ).result()
    )
    if not rows:
        return None
    row = rows[0]
    return ExtractionRun(
        run_id=row.run_id,
        prompt_name=row.prompt_name,
        prompt_version=row.prompt_version,
        model=row.model,
        split=row.split,
        paper_ids=tuple(row.paper_ids),
    )


@dataclass(frozen=True, slots=True)
class RunHistory:
    """A run from the run list, with when it ran, what it cost and when it was promoted."""

    run: ExtractionRun
    started_at: datetime | None
    cost_usd: float | None
    promoted_at: datetime | None


def read_runs(client: bigquery.Client, dataset_id: str) -> list[RunHistory]:
    """Every run in the run list, oldest first."""
    rows = client.query(f"SELECT * FROM `{dataset_id}.{RUNS_TABLE}` ORDER BY started_at").result()
    return [
        RunHistory(
            run=ExtractionRun(
                run_id=row.run_id,
                prompt_name=row.prompt_name,
                prompt_version=row.prompt_version,
                model=row.model,
                split=row.split,
                paper_ids=tuple(row.paper_ids),
            ),
            started_at=row.started_at,
            cost_usd=row.cost_usd,
            promoted_at=row.promoted_at,
        )
        for row in rows
    ]


def latest_promoted_run_id(client: bigquery.Client, dataset_id: str) -> str | None:
    """The run whose rows were last copied to production, or None if none was yet."""
    rows = list(
        client.query(
            f"SELECT run_id FROM `{dataset_id}.{RUNS_TABLE}` WHERE promoted_at IS NOT NULL"
            " ORDER BY promoted_at DESC LIMIT 1"
        ).result()
    )
    return rows[0].run_id if rows else None


def read_run_rows(client: bigquery.Client, dataset_id: str, run_id: str) -> SheetRows:
    """Return a run's rows keyed by (paper ID, table), with workbook headers as keys."""
    rows: SheetRows = defaultdict(list)
    for sheet_table in SHEET_TABLES:
        headers = _HEADERS_BY_COLUMN[sheet_table.table]
        # Ordered so a paper's rows get the same numbers each time they are judged; the
        # JSON starts with the IDs (source, material, experiment), so they sort first
        query = (
            f"SELECT * FROM `{dataset_id}.{sheet_table.table}` AS t WHERE run_id = @run_id"
            " ORDER BY TO_JSON_STRING(t)"
        )
        for row in client.query(query, job_config=_params(run_id=run_id)).result():
            record = {header: row.get(column) for column, header in headers.items()}
            rows[(row.source_id, sheet_table.table)].append(record)
    return dict(rows)


def replace_evaluation(
    client: bigquery.Client,
    dataset_id: str,
    run_id: str,
    judge: tuple[str, int],
    judged_at: datetime,
    cells: Sequence[JudgedCell],
    tables: Sequence[JudgedTable],
) -> None:
    """Replace one judge's verdicts on a run; judge is (model, prompt version)."""
    model, version = judge
    for name in (EVAL_CELLS_TABLE, EVAL_TABLES_TABLE):
        client.query(
            f"DELETE FROM `{dataset_id}.{name}` WHERE run_id = @run_id"
            " AND judge_model = @model AND judge_prompt_version = @version",
            job_config=_params(run_id=run_id, model=model, version=version),
        ).result()
    shared = {
        "run_id": run_id,
        "judge_model": model,
        "judge_prompt_version": version,
        "judged_at": judged_at.isoformat(),
    }
    cell_rows = [
        {**shared, **asdict(cell), "sheet_table": cell.table, "column_name": cell.column}
        for cell in cells
    ]
    for row in cell_rows:
        del row["table"], row["column"]
    table_rows = [{**shared, **asdict(table), "sheet_table": table.table} for table in tables]
    for row in table_rows:
        del row["table"]
    _load(client, f"{dataset_id}.{EVAL_CELLS_TABLE}", cell_rows, EVAL_CELLS_SCHEMA)
    _load(client, f"{dataset_id}.{EVAL_TABLES_TABLE}", table_rows, EVAL_TABLES_SCHEMA)


def read_evaluation(
    client: bigquery.Client,
    dataset_id: str,
    run_id: str,
    judge: tuple[str, int],
    paper_ids: Collection[str],
) -> tuple[list[JudgedCell], list[JudgedTable]]:
    """Return one judge's verdicts on a run, for the given papers only."""
    model, version = judge
    config = _params(run_id=run_id, model=model, version=version, ids=list(paper_ids))
    where = (
        "WHERE run_id = @run_id AND judge_model = @model AND judge_prompt_version = @version"
        " AND source_id IN UNNEST(@ids)"
    )
    cells = [
        JudgedCell(
            source_id=row.source_id,
            table=row.sheet_table,
            column=row.column_name,
            reference_row=row.reference_row,
            extracted_row=row.extracted_row,
            reference_value=row.reference_value,
            extracted_value=row.extracted_value,
            outcome=row.outcome,
            reason=row.reason or "",
            adjudication=row.adjudication,
            adjudication_reason=row.adjudication_reason or "",
        )
        for row in client.query(
            f"SELECT * FROM `{dataset_id}.{EVAL_CELLS_TABLE}` {where}", job_config=config
        ).result()
    ]
    tables = [
        JudgedTable(
            source_id=row.source_id,
            table=row.sheet_table,
            reference_rows=row.reference_rows,
            extracted_rows=row.extracted_rows,
            reference_filled=row.reference_filled,
            extracted_filled=row.extracted_filled,
            matched_rows=row.matched_rows,
        )
        for row in client.query(
            f"SELECT * FROM `{dataset_id}.{EVAL_TABLES_TABLE}` {where}", job_config=config
        ).result()
    ]
    return cells, tables


def promote_run(
    client: bigquery.Client,
    runs_dataset_id: str,
    production_dataset_id: str,
    run_id: str,
    paper_ids: Collection[str],
) -> None:
    """Replace the papers' production rows with the run's rows, and mark the run promoted.

    One transaction, so production never holds half a promotion.
    """
    statements = ["BEGIN TRANSACTION;"]
    for sheet_table in SHEET_TABLES:
        columns = ", ".join(field.name for field in build_schema(sheet_table))
        production = f"`{production_dataset_id}.{sheet_table.table}`"
        statements += [
            f"DELETE FROM {production} WHERE source_id IN UNNEST(@ids);",
            f"INSERT INTO {production} ({columns}) SELECT {columns}"
            f" FROM `{runs_dataset_id}.{sheet_table.table}`"
            " WHERE run_id = @run_id AND source_id IN UNNEST(@ids);",
        ]
    statements += [
        f"UPDATE `{runs_dataset_id}.{RUNS_TABLE}` SET promoted_at = CURRENT_TIMESTAMP()"
        " WHERE run_id = @run_id;",
        "COMMIT TRANSACTION;",
    ]
    client.query(
        "\n".join(statements), job_config=_params(run_id=run_id, ids=list(paper_ids))
    ).result()

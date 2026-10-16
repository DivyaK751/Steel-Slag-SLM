from unittest.mock import MagicMock

import pytest

from steel_slag_slm.data.bigquery_store import build_merge_sql, merge_updates, query_rows


def test_build_merge_sql_updates_only_given_columns() -> None:
    sql = build_merge_sql("proj.ds.papers", "openalex_id", ["relevant", "relevance_score"])
    assert "MERGE `proj.ds.papers`" in sql
    assert "ON target.openalex_id = source.openalex_id" in sql
    assert "relevant = source.relevant" in sql
    assert "relevance_score = source.relevance_score" in sql


@pytest.mark.parametrize(
    ("table", "column"), [("proj.ds.papers; DROP", "relevant"), ("proj.ds.papers", "a b")]
)
def test_build_merge_sql_rejects_unsafe_identifiers(table: str, column: str) -> None:
    with pytest.raises(ValueError, match="Unsafe"):
        build_merge_sql(table, "openalex_id", [column])


def test_merge_updates_sends_rows_as_one_struct_array_parameter() -> None:
    client = MagicMock()
    client.query.return_value.num_dml_affected_rows = 2
    rows = [
        {"openalex_id": "W1", "relevance_score": 0.9},
        {"openalex_id": "W2", "relevance_score": None},
    ]
    changed = merge_updates(
        client,
        "p.d.t",
        "openalex_id",
        rows,
        {"openalex_id": "STRING", "relevance_score": "FLOAT64"},
    )
    assert changed == 2
    (param,) = client.query.call_args.kwargs["job_config"].query_parameters
    assert param.name == "rows" and len(param.values) == 2


def test_merge_updates_skips_query_when_no_rows() -> None:
    client = MagicMock()
    assert merge_updates(client, "p.d.t", "openalex_id", [], {"openalex_id": "STRING"}) == 0
    client.query.assert_not_called()


def test_query_rows_types_scalar_and_list_parameters() -> None:
    client = MagicMock()
    client.query.return_value.result.return_value = []
    query_rows(client, "SELECT 1", {"limit": 5, "ids": ["W1"], "empty": []})
    params = {p.name: p for p in client.query.call_args.kwargs["job_config"].query_parameters}
    assert params["limit"].type_ == "INT64"
    assert params["ids"].array_type == "STRING"
    assert params["empty"].values == []

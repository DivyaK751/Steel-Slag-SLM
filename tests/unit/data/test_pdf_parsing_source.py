from typing import Any
from unittest.mock import MagicMock

import pytest

from steel_slag_slm.data import pdf_parsing_source


def test_list_pdf_sources_returns_rows_from_the_configured_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[str] = []
    rows = [{"openalex_id": "W123", "drive_link": "https://drive.google.com/file/d/F123/view"}]

    def fake_query_rows(_client: object, sql: str, _params: Any = None) -> list[dict[str, str]]:
        queries.append(sql)
        return rows

    monkeypatch.setattr(pdf_parsing_source, "query_rows", fake_query_rows)

    sources = pdf_parsing_source.list_pdf_sources(
        MagicMock(),
        "capstone-project-511200.steel_slag.screening_evaluation",
    )

    assert [(source.openalex_id, source.drive_link) for source in sources] == [
        ("W123", "https://drive.google.com/file/d/F123/view")
    ]
    assert "`capstone-project-511200.steel_slag.screening_evaluation`" in queries[0]
    assert "drive_link IS NOT NULL" in queries[0]
    assert "relevant" not in queries[0]


def test_list_pdf_sources_keeps_only_papers_with_the_given_screening_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Any]] = []

    def fake_query_rows(_client: object, sql: str, params: Any = None) -> list[dict[str, str]]:
        calls.append((sql, params))
        return []

    monkeypatch.setattr(pdf_parsing_source, "query_rows", fake_query_rows)

    pdf_parsing_source.list_pdf_sources(MagicMock(), "proj.steel_slag.papers", ["Yes", "Unclear"])

    sql, params = calls[0]
    assert "relevant IN UNNEST(@relevant_labels)" in sql
    assert params == {"relevant_labels": ["Yes", "Unclear"]}


def test_list_pdf_sources_rejects_invalid_table_id() -> None:
    with pytest.raises(ValueError, match="Invalid BigQuery table ID"):
        pdf_parsing_source.list_pdf_sources(MagicMock(), "screening_evaluation; DROP TABLE papers")

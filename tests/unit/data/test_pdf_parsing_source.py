from unittest.mock import MagicMock

import pytest

from steel_slag_slm.data import pdf_parsing_source


def test_list_pdf_sources_returns_rows_from_the_configured_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[str] = []
    rows = [{"openalex_id": "W123", "drive_link": "https://drive.google.com/file/d/F123/view"}]

    def fake_query_rows(_client: object, sql: str) -> list[dict[str, str]]:
        queries.append(sql)
        return rows

    monkeypatch.setattr(pdf_parsing_source, "query_rows", fake_query_rows)

    sources = pdf_parsing_source.list_pdf_sources(
        MagicMock(),
        "capstone-project-511200.steel_slag.papers_trial",
    )

    assert [(source.openalex_id, source.drive_link) for source in sources] == [
        ("W123", "https://drive.google.com/file/d/F123/view")
    ]
    assert "`capstone-project-511200.steel_slag.papers_trial`" in queries[0]
    assert "drive_link IS NOT NULL" in queries[0]


def test_list_pdf_sources_rejects_invalid_table_id() -> None:
    with pytest.raises(ValueError, match="Invalid BigQuery table ID"):
        pdf_parsing_source.list_pdf_sources(MagicMock(), "papers_trial; DROP TABLE papers")

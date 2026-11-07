import sys
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import steel_slag_slm.cli as cli


@pytest.mark.parametrize("paper_ids", [None, ["W123", "W456"]])
def test_parse_pdfs_builds_clients_and_runs_pipeline(monkeypatch, capsys, paper_ids) -> None:
    parsing_settings = MagicMock(output_folder_name="_parsed_approved")
    figure_settings = object()
    drive_settings = MagicMock(
        folder_id="source-folder",
        service_account_file=None,
        oauth_client_file=None,
        oauth_token_file=Path("/tmp/token.json"),
    )
    gcp_settings = MagicMock(project="billing-project", credentials_file=None)
    calls: dict[str, object] = {}

    argv = ["parse-pdfs", "--max-papers", "1"]
    if paper_ids is not None:
        argv += ["--paper-ids", *paper_ids]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(cli, "load_pdf_parsing_settings", lambda: parsing_settings)
    monkeypatch.setattr(cli, "load_figure_extraction_settings", lambda: figure_settings)
    monkeypatch.setattr(cli, "load_drive_connection_settings", lambda: drive_settings)
    monkeypatch.setattr(cli, "load_gcp_settings", lambda: gcp_settings)
    monkeypatch.setattr(cli, "build_drive_service", lambda *args: "drive-client")
    monkeypatch.setattr(cli, "build_bigquery_client", lambda *args: "bigquery-client")

    def fake_run(*args, **kwargs):
        calls["pipeline_args"] = args
        calls["pipeline_kwargs"] = kwargs
        return Counter(parsed=2)

    monkeypatch.setattr(cli, "run_pdf_parsing", fake_run)

    cli.parse_pdfs()

    assert calls["pipeline_args"] == (
        parsing_settings,
        "bigquery-client",
        "drive-client",
        "source-folder",
        1,
        paper_ids,
    )
    out = capsys.readouterr().out
    assert out.startswith("Parsing summary (Drive folder _parsed_approved/)")
    assert "      2  Parsed in this run" in out

    assert calls["pipeline_kwargs"] == {
        "figure_settings": figure_settings,
        "reparse": False,
        "retry_skipped": False,
        "store_images": True,
        "workers": 1,
    }

import sys
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock

import steel_slag_slm.cli as cli


def test_parse_pdfs_builds_clients_and_runs_pipeline(monkeypatch, capsys) -> None:
    parsing_settings = object()
    drive_settings = MagicMock(
        folder_id="source-folder",
        service_account_file=None,
        oauth_client_file=None,
        oauth_token_file=Path("/tmp/token.json"),
    )
    gcp_settings = MagicMock(project="billing-project", credentials_file=None)
    calls: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["parse-pdfs", "--max-papers", "1"])
    monkeypatch.setattr(cli, "load_pdf_parsing_settings", lambda: parsing_settings)
    monkeypatch.setattr(cli, "load_drive_connection_settings", lambda: drive_settings)
    monkeypatch.setattr(cli, "load_gcp_settings", lambda: gcp_settings)
    monkeypatch.setattr(cli, "build_drive_service", lambda *args: "drive-client")
    monkeypatch.setattr(cli, "build_bigquery_client", lambda *args: "bigquery-client")

    def fake_run(*args):
        calls["pipeline_args"] = args
        return Counter(parsed=2)

    monkeypatch.setattr(cli, "run_pdf_parsing", fake_run)

    cli.parse_pdfs()

    assert calls["pipeline_args"] == (
        parsing_settings,
        "bigquery-client",
        "drive-client",
        "source-folder",
        1,
        None,
    )
    assert capsys.readouterr().out == "Summary: {'parsed': 2}\n"

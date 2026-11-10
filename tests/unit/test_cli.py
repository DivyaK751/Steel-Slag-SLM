import sys
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import steel_slag_slm.cli as cli


def _fake_parse_pdfs_clients(monkeypatch, calls: dict[str, object]) -> MagicMock:
    """Stub settings, clients and indexing for parse-pdfs; return the parsing settings."""
    parsing_settings = MagicMock(output_folder_name="_parsed_approved")
    drive_settings = MagicMock(
        folder_id="source-folder",
        service_account_file=None,
        oauth_client_file=None,
        oauth_token_file=Path("/tmp/token.json"),
    )
    gcp_settings = MagicMock(project="billing-project", credentials_file=None)
    monkeypatch.setattr(cli, "load_pdf_parsing_settings", lambda: parsing_settings)
    monkeypatch.setattr(cli, "load_drive_connection_settings", lambda: drive_settings)
    monkeypatch.setattr(cli, "load_gcp_settings", lambda: gcp_settings)
    monkeypatch.setattr(cli, "build_drive_service", lambda *args: "drive-client")
    monkeypatch.setattr(cli, "build_bigquery_client", lambda *args: "bigquery-client")
    monkeypatch.setattr(cli, "find_subfolder_id", lambda _service, _parent, name: f"id-{name}")

    def fake_index(_service, parsed_folder_id, *args):
        calls["indexed_folder"] = parsed_folder_id
        return 0

    monkeypatch.setattr(cli, "_index_parsed_folder", fake_index)
    return parsing_settings


@pytest.mark.parametrize(("flag", "budget"), [([], 60.0), (["--max-gemini-usd", "5"], 5.0)])
def test_parse_pdfs_reads_figures_with_gemini_by_default(monkeypatch, capsys, flag, budget) -> None:
    built: dict[str, object] = {}

    class FakeReader:
        spent_usd = 1.234
        stop_reason = None

        def __init__(self, _runner, _prompt, _settings, max_run_usd) -> None:
            built["budget"] = max_run_usd

        def close(self) -> None:
            built["closed"] = True

    reading_settings = MagicMock(prompt_name="read_figure", max_run_usd=60.0)
    _fake_parse_pdfs_clients(monkeypatch, {})
    monkeypatch.setattr(sys, "argv", ["parse-pdfs", *flag])
    monkeypatch.setattr(cli, "load_figure_extraction_settings", MagicMock)
    monkeypatch.setattr(cli, "load_figure_reading_settings", lambda: reading_settings)
    monkeypatch.setattr(cli, "load_prompt", lambda _name: "prompt")
    monkeypatch.setattr(cli, "build_figure_reader_agent", lambda *_: "agent")
    monkeypatch.setattr(cli, "build_runner", lambda _agent: "runner")
    monkeypatch.setattr(cli, "BackgroundFigureReader", FakeReader)

    def fake_run(*_args, **kwargs):
        built["reader"] = kwargs["figure_reader"]
        return Counter(parsed=1)

    monkeypatch.setattr(cli, "run_pdf_parsing", fake_run)

    cli.parse_pdfs()

    assert isinstance(built["reader"], FakeReader)
    assert built["budget"] == budget and built["closed"] is True
    assert "Gemini cost of figure reading in this run: about $1.23" in capsys.readouterr().out


@pytest.mark.parametrize("paper_ids", [None, ["W123", "W456"]])
def test_parse_pdfs_builds_clients_and_runs_pipeline(monkeypatch, capsys, paper_ids) -> None:
    figure_settings = object()
    calls: dict[str, object] = {}
    parsing_settings = _fake_parse_pdfs_clients(monkeypatch, calls)

    argv = ["parse-pdfs", "--max-papers", "1", "--no-figure-reading"]
    if paper_ids is not None:
        argv += ["--paper-ids", *paper_ids]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(cli, "load_figure_extraction_settings", lambda: figure_settings)

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
        "figure_reader": None,
    }


def test_parse_pdfs_indexes_the_folder_it_parsed_into(monkeypatch) -> None:
    calls: dict[str, object] = {}
    _fake_parse_pdfs_clients(monkeypatch, calls)
    monkeypatch.setattr(sys, "argv", ["parse-pdfs", "--no-figure-reading"])
    monkeypatch.setattr(cli, "load_figure_extraction_settings", object)
    monkeypatch.setattr(cli, "run_pdf_parsing", lambda *args, **kwargs: Counter(parsed=1))

    cli.parse_pdfs()

    assert calls["indexed_folder"] == "id-_parsed_approved"


def test_parse_pdfs_skip_index_leaves_the_index_alone(monkeypatch) -> None:
    calls: dict[str, object] = {}
    _fake_parse_pdfs_clients(monkeypatch, calls)
    monkeypatch.setattr(sys, "argv", ["parse-pdfs", "--no-figure-reading", "--skip-index"])
    monkeypatch.setattr(cli, "load_figure_extraction_settings", object)
    monkeypatch.setattr(cli, "run_pdf_parsing", lambda *args, **kwargs: Counter(parsed=1))

    cli.parse_pdfs()

    assert "indexed_folder" not in calls

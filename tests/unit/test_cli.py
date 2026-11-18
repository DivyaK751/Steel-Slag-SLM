import sys
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import steel_slag_slm.cli as cli


def test_audit_captured_tables_uses_entire_benchmark(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit-captured-tables",
            "--benchmark",
            "split.yaml",
            "--pdf-dir",
            "pdfs",
            "--captures-dir",
            "captures",
            "--out",
            "audit.json",
        ],
    )
    monkeypatch.setattr(cli, "load_benchmark_split", lambda _: MagicMock(dev=["W1"], test=["W2"]))
    paper = MagicMock(physical_pages=3, tables=[MagicMock(candidate_row_indices=[1, 2])])
    run = MagicMock(return_value=MagicMock(papers=[paper]))
    monkeypatch.setattr(cli, "audit_table_corpus", run)
    cli.audit_captured_tables()
    run.assert_called_once_with(["W1", "W2"], Path("pdfs"), Path("captures"), Path("audit.json"))
    assert (
        "1 papers, 3 pages, 1 detected tables, 1 with candidate rows; unreviewed"
        in capsys.readouterr().out
    )


@pytest.mark.parametrize("selection", [[], ["--paper-ids", "W123"]])
def test_capture_table_corpus_selects_only_benchmark_inputs(monkeypatch, selection) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "capture-table-corpus",
            "--benchmark",
            "split.yaml",
            "--pdf-dir",
            "pdfs",
            "--out-dir",
            "out",
            *selection,
        ],
    )
    monkeypatch.setattr(
        cli, "load_benchmark_split", lambda _: MagicMock(dev=["W123"], test=["W456"])
    )
    monkeypatch.setattr(cli, "build_docling_converter", lambda **_: "converter")
    run = MagicMock(return_value=[])
    monkeypatch.setattr(cli, "capture_local_corpus", run)
    cli.capture_table_corpus()
    expected = ["W123"] if selection else ["W123", "W456"]
    run.assert_called_once_with(expected, Path("pdfs"), Path("out"), "converter")


def test_capture_table_corpus_rejects_ids_outside_benchmark(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "capture-table-corpus",
            "--benchmark",
            "split.yaml",
            "--pdf-dir",
            "pdfs",
            "--out-dir",
            "out",
            "--paper-ids",
            "W999",
        ],
    )
    monkeypatch.setattr(cli, "load_benchmark_split", lambda _: MagicMock(dev=["W123"], test=[]))
    with pytest.raises(ValueError, match="outside benchmark"):
        cli.capture_table_corpus()


@pytest.mark.parametrize("has_rows", [False, True])
def test_budget_table_evidence_exposes_empty_papers(monkeypatch, has_rows) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "budget-table-evidence",
            "--inspection-dir",
            "in",
            "--out-dir",
            "out",
            "--embedding-limit",
            "512",
            "--reranker-limit",
            "512",
            "--query-reserve",
            "64",
        ],
    )
    settings = MagicMock(model="embed", reranker="rerank")
    monkeypatch.setattr(cli, "load_embedding_settings", lambda: settings)
    run = MagicMock(return_value=[MagicMock(tables=[MagicMock(rows=[1] if has_rows else [])])])
    monkeypatch.setattr(cli, "budget_local_table_evidence", run)
    if has_rows:
        cli.budget_table_evidence()
    else:
        with pytest.raises(SystemExit, match="no budgeted rows"):
            cli.budget_table_evidence()
    assert run.call_args.args[2].query_reserve == 64
    assert run.call_args.args[3:] == ("embed", "rerank")


@pytest.mark.parametrize("has_rows", [False, True])
def test_render_table_evidence_reports_blocked_papers(monkeypatch, has_rows) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["render-table-evidence", "--inspection-dir", "in", "--out-dir", "out"],
    )
    report = MagicMock(tables=[MagicMock(rows=[1] if has_rows else [], issues=[])])
    render = MagicMock(return_value=[report])
    monkeypatch.setattr(cli, "render_local_table_evidence", render)
    if has_rows:
        cli.render_table_evidence()
    else:
        with pytest.raises(SystemExit, match="no candidate rows"):
            cli.render_table_evidence()
    render.assert_called_once_with(Path("in"), Path("out"))


@pytest.mark.parametrize("status", ["captured", "no_tables"])
def test_inspect_tables_exposes_empty_page_failure(monkeypatch, status) -> None:
    report = MagicMock(status=status)
    monkeypatch.setattr(
        sys,
        "argv",
        ["inspect-tables", "--cases", "cases.yaml", "--pdf-dir", "pdfs", "--out-dir", "out"],
    )
    load_cases = MagicMock(return_value=["case"])
    build = MagicMock(return_value="converter")
    inspect = MagicMock(return_value=[report])
    monkeypatch.setattr(cli, "load_table_inspection_cases", load_cases)
    monkeypatch.setattr(cli, "build_docling_converter", build)
    monkeypatch.setattr(cli, "inspect_local_tables", inspect)
    if status == "no_tables":
        with pytest.raises(SystemExit, match="no tables"):
            cli.inspect_tables()
    else:
        cli.inspect_tables()
    load_cases.assert_called_once_with(Path("cases.yaml"))
    build.assert_called_once_with(include_figures=True)
    inspect.assert_called_once_with(["case"], Path("pdfs"), Path("out"), "converter")


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

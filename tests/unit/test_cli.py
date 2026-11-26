import sys
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import steel_slag_slm.cli as cli
from steel_slag_slm.pipelines.describe_tables import TableDescriptionReport


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


def _fake_describe_tables_clients(monkeypatch, calls: dict[str, object]) -> None:
    """Stub settings, Drive, Gemini and the pipeline for describe-tables."""
    settings = MagicMock(prompt_name="describe_table", max_run_usd=20.0)
    monkeypatch.setattr(cli, "load_table_description_settings", lambda: settings)
    monkeypatch.setattr(
        cli, "load_gcp_settings", lambda: MagicMock(credentials_file=Path("/tmp/gcp.json"))
    )
    monkeypatch.setattr(
        cli,
        "load_pdf_parsing_settings",
        lambda: MagicMock(output_folder_name="_parsed_approved", index_file_name="index.json"),
    )
    monkeypatch.setattr(cli, "connect_to_parsed_folder", lambda name: ("drive", f"id-{name}"))
    monkeypatch.setattr(
        cli, "load_drive_connection_settings", lambda: MagicMock(folder_id="source-folder")
    )
    monkeypatch.setattr(cli, "list_paper_titles", lambda _service, _folder: {"W1": "A title"})
    monkeypatch.setattr(cli, "load_prompt", lambda _name: "prompt")
    monkeypatch.setattr(cli, "build_table_describer_agent", lambda *_: "agent")
    monkeypatch.setattr(cli, "build_runner", lambda _agent: "runner")

    def fake_run(service, folder_id, index_name, runner, prompt, _settings, **kwargs):
        calls.update(kwargs, folder_id=folder_id, index_name=index_name, runner=runner)
        return TableDescriptionReport(outcomes=Counter(calls.pop("outcomes", {})))

    monkeypatch.setattr(cli, "run_table_description", fake_run)


@pytest.mark.parametrize(
    ("flags", "budget", "seconds"),
    [([], 20.0, None), (["--max-gemini-usd", "3", "--max-minutes", "150"], 3.0, 9000.0)],
)
def test_describe_tables_runs_on_the_configured_folder(
    monkeypatch, capsys, flags, budget, seconds
) -> None:
    calls: dict[str, object] = {"outcomes": {"described": 2}}
    _fake_describe_tables_clients(monkeypatch, calls)
    monkeypatch.setattr(sys, "argv", ["describe-tables", *flags])
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

    cli.describe_tables()

    assert calls["folder_id"] == "id-_parsed_approved" and calls["index_name"] == "index.json"
    assert calls["max_run_usd"] == budget and calls["max_seconds"] == seconds
    assert calls["titles"] == {"W1": "A title"}
    assert cli.os.environ["GOOGLE_APPLICATION_CREDENTIALS"] == "/tmp/gcp.json"
    assert "2  described" in capsys.readouterr().out


def test_describe_tables_fails_when_a_paper_failed(monkeypatch) -> None:
    _fake_describe_tables_clients(monkeypatch, {"outcomes": {"failed": 1}})
    monkeypatch.setattr(sys, "argv", ["describe-tables", "--paper-ids", "W1"])
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "unused")

    with pytest.raises(SystemExit, match="1 paper\\(s\\) failed"):
        cli.describe_tables()


@pytest.mark.parametrize(
    ("outcomes", "complete"),
    [({"complete": 3}, True), ({"incomplete": 1, "tables_missing": 2}, False)],
)
def test_describe_tables_check_fails_until_every_table_is_described(
    monkeypatch, outcomes, complete
) -> None:
    calls: dict[str, object] = {"outcomes": outcomes}
    _fake_describe_tables_clients(monkeypatch, calls)
    monkeypatch.setattr(sys, "argv", ["describe-tables", "--check"])
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "unused")

    if complete:
        cli.describe_tables()
    else:
        with pytest.raises(SystemExit, match="2 table\\(s\\) still without a description"):
            cli.describe_tables()
    assert calls["check_only"] is True


def test_describe_tables_rejects_a_nonpositive_time_limit(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["describe-tables", "--max-minutes", "0"])

    with pytest.raises(SystemExit):
        cli.describe_tables()


def _fake_answer_setup(monkeypatch, pieces: list[str]) -> dict[str, object]:
    """Stub the gateway and settings for the answer command; return what it was called with."""
    calls: dict[str, object] = {}
    gateway = MagicMock(answer_model="gemini", models={"gemini": {}, "qwen3-8b": {}})
    monkeypatch.setattr(cli, "load_llm_gateway_settings", lambda: gateway)
    monkeypatch.setattr(cli, "load_answer_settings", lambda: MagicMock(prompt_name="answer"))
    monkeypatch.setattr(cli, "load_prompt", lambda name: f"prompt:{name}")
    monkeypatch.setattr(cli, "GatewayClient", lambda _settings, model: f"client:{model}")

    def fake_write(question, evidence, client, prompt, _settings):
        calls.update(question=question, evidence=evidence, client=client, prompt=prompt)
        yield from pieces

    monkeypatch.setattr(cli, "write_answer", fake_write)
    return calls


def test_answer_streams_the_answer_and_reports_unknown_citations(
    monkeypatch, capsys, tmp_path
) -> None:
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("[S1] W123 ...", encoding="utf-8")
    calls = _fake_answer_setup(monkeypatch, ["42 MPa ", "[S1][P4]."])
    monkeypatch.setattr(
        sys, "argv", ["answer", "Strength?", "--evidence", str(evidence), "--model", "qwen3-8b"]
    )

    cli.answer()

    assert calls == {
        "question": "Strength?",
        "evidence": "[S1] W123 ...",
        "client": "client:qwen3-8b",
        "prompt": "prompt:answer",
    }
    out = capsys.readouterr().out
    assert out.startswith("42 MPa [S1][P4].")
    assert "[qwen3-8b] Unknown citations: P4" in out


def test_answer_rejects_a_model_the_gateway_does_not_list(monkeypatch, tmp_path) -> None:
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("E", encoding="utf-8")
    _fake_answer_setup(monkeypatch, [])
    monkeypatch.setattr(
        sys, "argv", ["answer", "Q", "--evidence", str(evidence), "--model", "gpt-9"]
    )

    with pytest.raises(SystemExit):
        cli.answer()

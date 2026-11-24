import json
from collections import Counter
from typing import Any

import pytest

from steel_slag_slm.config import ModelPrices, TableDescriptionSettings
from steel_slag_slm.data.parsed_manifest import ParsedManifest, has_retired_fields
from steel_slag_slm.exceptions import AgentOutputError
from steel_slag_slm.llm.prompts import PromptTemplate
from steel_slag_slm.pipelines import describe_tables
from steel_slag_slm.schemas import TableDescription, TableToDescribe, TokenUsage

PROMPT = PromptTemplate("describe_table", 1, "d", "i", "{table}", ("table",))
SETTINGS = TableDescriptionSettings(
    model="gemini-test",
    temperature=0.0,
    prompt_name="describe_table",
    max_concurrency=2,
    call_timeout_s=5,
    prices=ModelPrices(input_usd_per_million=1.0, output_usd_per_million=0.0),
    context_max_chars=1000,
)
USAGE = TokenUsage(input_tokens=2000)  # $0.002 at the test price
DESCRIPTION = TableDescription(summary="Oxide composition of the steel slag.")


@pytest.fixture
def described(monkeypatch: pytest.MonkeyPatch) -> list[TableToDescribe]:
    """Answer every table with DESCRIPTION, and record which tables were sent."""
    sent: list[TableToDescribe] = []

    async def fake_describe(_runner: Any, _prompt: Any, table: TableToDescribe) -> Any:
        sent.append(table)
        return DESCRIPTION, USAGE

    monkeypatch.setattr(describe_tables, "describe_table", fake_describe)
    return sent


def _publish(drive: Any, record: dict[str, Any], **entry: Any) -> str:
    """Store a paper's parsed.json and an index entry for it; return the file's ID."""
    content = json.dumps(record).encode()
    folder = f"paper-{record['openalex_id']}"
    file_id: str = drive.write_file(None, folder, "parsed.json", content, "application/json")
    papers = {
        record["openalex_id"]: {
            "json_file_id": file_id,
            "image_file_ids": [],
            "paper_folder_id": folder,
            **entry,
        }
    }
    index = json.dumps({"schema_version": 1, "papers": papers}).encode()
    drive.write_file(None, "parsed", "index.json", index, "application/json")
    return file_id


def _run(**kwargs: Any) -> describe_tables.TableDescriptionReport:
    return describe_tables.run_table_description(
        None,
        "parsed",
        "index.json",
        None,
        PROMPT,
        SETTINGS,
        **kwargs,  # type: ignore[arg-type]
    )


def test_describes_each_table_once_and_a_rerun_sends_nothing(
    fake_drive: Any, manifest_record: dict[str, Any], described: list[TableToDescribe]
) -> None:
    file_id = _publish(fake_drive, manifest_record)

    report = _run()
    rerun = _run()

    assert [table.block_index for table in described] == [1]
    stored = ParsedManifest.model_validate_json(fake_drive.contents[file_id])
    assert [d.description for d in stored.table_descriptions] == [DESCRIPTION]
    assert report.outcomes == {"described": 1, "tables_described": 1, "tables_total": 1}
    assert report.spent_usd == pytest.approx(0.002)
    assert rerun.outcomes == {"up_to_date": 1, "tables_total": 1}


def test_a_described_paper_with_retired_rows_is_rewritten_without_them(
    fake_drive: Any, manifest_record: dict[str, Any], described: list[TableToDescribe]
) -> None:
    file_id = _publish(fake_drive, manifest_record)
    _run()
    record = json.loads(fake_drive.contents[file_id])
    fake_drive.contents[file_id] = json.dumps({**record, "contextual_tables": []}).encode()

    report = _run()

    assert report.outcomes == {"cleaned": 1, "tables_total": 1}
    assert not has_retired_fields(fake_drive.contents[file_id])
    assert len(described) == 1  # the description was kept, not requested again


def test_a_failed_table_is_left_for_the_next_run(
    fake_drive: Any, manifest_record: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    file_id = _publish(fake_drive, manifest_record)
    before = fake_drive.contents[file_id]

    async def invented(_runner: Any, _prompt: Any, _table: Any) -> Any:
        raise AgentOutputError("Description states numbers the table does not print", usage=USAGE)

    monkeypatch.setattr(describe_tables, "describe_table", invented)
    report = _run()

    assert report.outcomes == {
        "not_described": 1, "tables_failed": 1, "tables_total": 1, "tables_missing": 1
    }  # fmt: skip
    assert report.spent_usd == pytest.approx(0.002)  # the rejected answer was still billed
    assert fake_drive.contents[file_id] == before


def test_a_spent_budget_stops_the_run_before_any_download(
    fake_drive: Any, manifest_record: dict[str, Any], described: list[TableToDescribe]
) -> None:
    _publish(fake_drive, manifest_record)

    report = _run(max_run_usd=0.0)

    assert described == []
    assert report.outcomes == {"not_started": 1}
    assert report.stop_reason == "reached the $0.00 Gemini budget for this run"


def test_a_file_rewritten_during_the_run_is_not_overwritten(
    fake_drive: Any, manifest_record: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    file_id = _publish(fake_drive, manifest_record)
    rewritten = json.dumps({**manifest_record, "markdown": "reparsed"}).encode()

    async def rewrite_meanwhile(_runner: Any, _prompt: Any, _table: Any) -> Any:
        fake_drive.contents[file_id] = rewritten
        return DESCRIPTION, USAGE

    monkeypatch.setattr(describe_tables, "describe_table", rewrite_meanwhile)
    report = _run()

    assert report.outcomes["changed_during_run"] == 1
    assert fake_drive.contents[file_id] == rewritten


def test_check_only_counts_missing_tables_and_retired_rows_without_gemini(
    fake_drive: Any, manifest_record: dict[str, Any], described: list[TableToDescribe]
) -> None:
    file_id = _publish(fake_drive, {**manifest_record, "contextual_tables": []})
    before = fake_drive.contents[file_id]

    report = _run(check_only=True)
    _run()
    after = _run(check_only=True)

    assert report.outcomes == {
        "incomplete": 1, "tables_total": 1, "tables_missing": 1, "retired_rows_left": 1
    }  # fmt: skip
    assert len(described) == 1  # only the real run called Gemini
    assert fake_drive.contents[file_id] != before
    assert after.outcomes == {"complete": 1, "tables_total": 1}


def test_only_requested_parsed_papers_are_checked(
    fake_drive: Any, manifest_record: dict[str, Any], described: list[TableToDescribe]
) -> None:
    _publish(fake_drive, manifest_record)

    with pytest.raises(ValueError, match="Not parsed in this folder: W2"):
        _run(paper_ids=["W1", "W2"])
    assert _run(paper_ids=["W1"]).outcomes["described"] == 1


def test_skipped_papers_are_not_checked(
    fake_drive: Any, manifest_record: dict[str, Any], described: list[TableToDescribe]
) -> None:
    _publish(fake_drive, manifest_record, status="skipped", skip_reason="empty PDF")

    assert _run().outcomes == {}
    assert described == []


def test_summary_lists_nonzero_outcomes_cost_and_why_the_run_stopped() -> None:
    report = describe_tables.TableDescriptionReport(
        outcomes=Counter(described=3, failed=1, tables_described=20, tables_missing=2),
        spent_usd=0.0412,
        stop_reason="reached the time limit for this run",
    )

    assert describe_tables.format_description_summary(report, "_parsed_approved") == (
        "Table descriptions (Drive folder _parsed_approved/)\n"
        "Papers:\n"
        "      3  described\n"
        "      1  failed\n"
        "Tables:\n"
        "     20  described in this run\n"
        "      2  still without a description\n"
        "Gemini cost: about $0.04\n"
        "Stopped early: reached the time limit for this run; rerun to continue"
    )

"""Describe the tables of parsed papers with Gemini and store the descriptions in parsed.json.

Runs over papers parsed earlier, without parsing or downloading PDFs again: each table's text
and the paper's explaining paragraphs are already in its parsed.json. Only tables without a
description from the current prompt version are sent, so a rerun picks up where one stopped.
"""

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from google.adk.runners import Runner

from steel_slag_slm.agents.runner import gather_bounded
from steel_slag_slm.agents.table_describer import describe_table, table_name
from steel_slag_slm.config import TableDescriptionSettings
from steel_slag_slm.data import drive_storage, parsed_index
from steel_slag_slm.data.parsed_manifest import (
    ParsedManifest,
    StoredTableDescription,
    has_retired_fields,
)
from steel_slag_slm.data.parsed_publication import publish_table_descriptions
from steel_slag_slm.data.table_context import tables_to_describe
from steel_slag_slm.exceptions import AgentOutputError
from steel_slag_slm.llm.costs import estimate_cost_usd
from steel_slag_slm.llm.prompts import PromptTemplate
from steel_slag_slm.schemas import TableToDescribe

logger = logging.getLogger(__name__)
# This many tables failing in a row means Gemini itself is unreachable (login, quota, outage)
MAX_FAILURE_STREAK = 25
# Papers being described at once; enough to keep every Gemini call slot busy
PAPERS_IN_FLIGHT = 8


@dataclass
class TableDescriptionReport:
    """What a run did: paper and table outcomes, its estimated cost, and why it stopped early."""

    outcomes: Counter[str] = field(default_factory=Counter)
    spent_usd: float = 0.0
    stop_reason: str | None = None


class _Run:
    """One run's shared state: the Gemini call limit, Drive lock, budget and failure streak."""

    def __init__(
        self,
        service: Any,
        runner: Runner,
        prompt: PromptTemplate,
        settings: TableDescriptionSettings,
        max_run_usd: float | None,
        deadline: float | None,
        titles: Mapping[str, str],
    ) -> None:
        self.service = service
        self.runner = runner
        self.prompt = prompt
        self.settings = settings
        self.max_run_usd = max_run_usd
        self.deadline = deadline
        self.titles = titles
        self.calls = asyncio.Semaphore(settings.max_concurrency)
        self.drive = asyncio.Lock()  # the Drive client is not thread-safe: one call at a time
        self.report = TableDescriptionReport()
        self.failure_streak = 0

    @property
    def stop_reason(self) -> str | None:
        """Why no more tables should be sent, or None while describing can go on."""
        if self.max_run_usd is not None and self.report.spent_usd >= self.max_run_usd:
            return f"reached the ${self.max_run_usd:.2f} Gemini budget for this run"
        if self.failure_streak >= MAX_FAILURE_STREAK:
            return f"{self.failure_streak} tables failed in a row; check Gemini access"
        if self.deadline is not None and time.monotonic() >= self.deadline:
            return "reached the time limit for this run"
        return None

    async def drive_call(self, func: Any, *args: Any) -> Any:
        async with self.drive:
            return await asyncio.to_thread(func, self.service, *args)


def run_table_description(
    service: Any,
    parsed_folder_id: str,
    index_file_name: str,
    runner: Runner,
    prompt: PromptTemplate,
    settings: TableDescriptionSettings,
    paper_ids: Sequence[str] | None = None,
    max_papers: int | None = None,
    max_run_usd: float | None = None,
    max_seconds: float | None = None,
    titles: Mapping[str, str] | None = None,
) -> TableDescriptionReport:
    """Describe every undescribed table of the parsed folder's papers, or only of paper_ids.

    Each paper's parsed.json is rewritten in place once its tables are done, keeping the
    descriptions that succeeded; failed tables are retried on the next run. A paper whose
    parsed.json changed while its tables were described is left for the next run. Stops
    sending tables once max_run_usd is spent, max_seconds have passed, or Gemini keeps failing.
    titles maps paper IDs to the titles Gemini is told, so it knows what each study is about.

    Raises:
        ValueError: If max_papers is not positive or a requested paper is not in the index.
    """
    if max_papers is not None and max_papers <= 0:
        raise ValueError("max_papers must be greater than zero")
    papers = _papers_to_check(
        parsed_index.load_index(service, parsed_folder_id, index_file_name), paper_ids
    )
    if max_papers is not None:
        papers = papers[:max_papers]
    deadline = time.monotonic() + max_seconds if max_seconds is not None else None
    run = _Run(service, runner, prompt, settings, max_run_usd, deadline, titles or {})
    logger.info("Checking the tables of %d parsed papers", len(papers))
    results = asyncio.run(
        gather_bounded(papers, lambda paper: _describe_paper(run, *paper), PAPERS_IN_FLIGHT)
    )
    for (paper_id, _), result in zip(papers, results, strict=True):
        if isinstance(result, Exception):
            # Unreadable or invalid parsed.json, or a failed Drive call: the next run retries
            logger.error("Could not describe the tables of %s: %s", paper_id, result)
            run.report.outcomes["failed"] += 1
        else:
            run.report.outcomes[result] += 1
    run.report.stop_reason = run.stop_reason
    return run.report


def _papers_to_check(
    papers: dict[str, dict[str, Any]], paper_ids: Sequence[str] | None
) -> list[tuple[str, dict[str, Any]]]:
    """Return the parsed papers' index entries, in ID order, limited to paper_ids if given."""
    parsed = {
        paper_id: entry
        for paper_id, entry in papers.items()
        if entry.get("status") != "skipped"
        and entry.get("json_file_id")
        and entry.get("paper_folder_id")
    }
    if paper_ids is None:
        return sorted(parsed.items())
    missing = sorted(set(paper_ids) - parsed.keys())
    if missing:
        raise ValueError(f"Not parsed in this folder: {', '.join(missing)}")
    return [(paper_id, parsed[paper_id]) for paper_id in sorted(set(paper_ids))]


async def _describe_paper(run: _Run, paper_id: str, entry: dict[str, Any]) -> str:
    """Describe one paper's undescribed tables and save them; return the paper's outcome."""
    if run.stop_reason is not None:
        return "not_started"  # left for the next run without even downloading it
    content = await run.drive_call(drive_storage.download_file, entry["json_file_id"])
    manifest = ParsedManifest.model_validate_json(content)
    if manifest.openalex_id != paper_id:
        raise ValueError("Indexed paper does not match its parsed.json")
    tables = tables_to_describe(
        manifest, run.prompt.version, run.settings.context_max_chars, run.titles.get(paper_id, "")
    )
    described = await asyncio.gather(*(_describe_one(run, table) for table in tables))
    new = [stored for stored in described if stored is not None]
    if not new and not has_retired_fields(content):
        return "up_to_date" if not tables else "not_described"

    # Optimistic check: another job (e.g. figure reading) may have rewritten the file meanwhile
    current = await run.drive_call(drive_storage.download_file, entry["json_file_id"])
    if current != content:
        logger.warning("%s: parsed.json changed during the run; next run retries it", paper_id)
        return "changed_during_run"
    await run.drive_call(publish_table_descriptions, entry["paper_folder_id"], manifest, new)
    logger.info("%s: described %d of %d tables", paper_id, len(new), len(tables))
    return "described" if new else "cleaned"


async def _describe_one(run: _Run, table: TableToDescribe) -> StoredTableDescription | None:
    """Describe one table; None when it was not described (failed, or the run stopped)."""
    async with run.calls:
        if run.stop_reason is not None:
            run.report.outcomes["tables_not_started"] += 1
            return None
        try:
            description, usage = await asyncio.wait_for(
                describe_table(run.runner, run.prompt, table), run.settings.call_timeout_s
            )
        # Bad answers, API errors and timeouts cost this table only; a rerun retries it
        except Exception as error:
            if isinstance(error, AgentOutputError) and error.usage is not None:
                run.report.spent_usd += estimate_cost_usd(error.usage, run.settings.prices)
            run.failure_streak += 1
            run.report.outcomes["tables_failed"] += 1
            detail = f"{type(error).__name__}: {error}".removesuffix(": ")
            logger.warning(
                "Could not describe %s %s: %s", table.paper_id, table_name(table), detail
            )
            return None

    run.failure_streak = 0
    run.report.spent_usd += estimate_cost_usd(usage, run.settings.prices)
    run.report.outcomes["tables_described"] += 1
    return StoredTableDescription(
        block_index=table.block_index,
        table_sha256=table.table_sha256,
        prompt_version=run.prompt.version,
        description=description,
    )


_PAPER_OUTCOMES = (
    ("described", "described"),
    ("cleaned", "retired table rows removed"),
    ("up_to_date", "already up to date"),
    ("not_described", "no table described (retried next run)"),
    ("changed_during_run", "changed during the run (retried next run)"),
    ("not_started", "not started (run stopped early)"),
    ("failed", "failed"),
)
_TABLE_OUTCOMES = (
    ("tables_described", "described"),
    ("tables_failed", "failed (retried next run)"),
    ("tables_not_started", "not started (run stopped early)"),
)


def format_description_summary(report: TableDescriptionReport, folder_name: str) -> str:
    """Summarize a run for the terminal and the Actions page; zero counts are left out."""
    lines = [f"Table descriptions (Drive folder {folder_name}/)", "Papers:"]
    lines += _count_lines(report.outcomes, _PAPER_OUTCOMES)
    lines += ["Tables:", *_count_lines(report.outcomes, _TABLE_OUTCOMES)]
    lines.append(f"Gemini cost: about ${report.spent_usd:.2f}")
    if report.stop_reason is not None:
        lines.append(f"Stopped early: {report.stop_reason}; rerun to continue")
    return "\n".join(lines)


def _count_lines(outcomes: Counter[str], labels: Sequence[tuple[str, str]]) -> list[str]:
    counted = [f"  {outcomes[key]:>5}  {label}" for key, label in labels if outcomes[key]]
    return counted or ["      0"]

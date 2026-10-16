"""Run the screening agent on human-labelled papers and save an Excel report of the run."""

import logging
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from google.adk.runners import Runner
from google.cloud import bigquery
from google.genai.errors import APIError

from steel_slag_slm.agents.relevance_screener import paper_from_row, screen_paper
from steel_slag_slm.agents.runner import gather_bounded
from steel_slag_slm.config import EvaluationSettings, ScreeningSettings
from steel_slag_slm.data.bigquery_store import query_rows
from steel_slag_slm.evaluation.excel_report import PredictionRow, write_report
from steel_slag_slm.evaluation.metrics import FAILED, EvalMetrics, compute_metrics, normalize_label
from steel_slag_slm.exceptions import AgentOutputError
from steel_slag_slm.llm.prompts import PromptTemplate
from steel_slag_slm.schemas import RelevanceDecision

logger = logging.getLogger(__name__)

# Failures that cost one paper, not the run; the paper shows up as "Failed" in the report
_PER_PAPER_ERRORS = (AgentOutputError, APIError)

_LABELLED_SQL = """
SELECT openalex_id, title, abstract, year, keywords, human_label
FROM `{table}`
WHERE openalex_id IS NOT NULL
ORDER BY openalex_id
"""


def build_prediction_row(
    row: Mapping[str, object], result: RelevanceDecision | Exception
) -> PredictionRow:
    """Line up one paper's human label with the agent's answer (or its failure)."""
    human = normalize_label(str(row.get("human_label") or ""))
    if isinstance(result, Exception):
        agent, decision = FAILED, None
    else:
        agent, decision = result.relevant, result
    match = "" if human is None else ("✓" if human == agent else "✗")
    return PredictionRow(
        openalex_id=str(row["openalex_id"]),
        title=str(row.get("title") or ""),
        human_label=human,
        agent_label=agent,
        match=match,
        relevance_score=decision.relevance_score if decision else None,
        relevance_confidence=decision.relevance_confidence if decision else None,
        relevance_reason=decision.relevance_reason if decision else str(result),
        supporting_excerpt=decision.supporting_excerpt if decision else None,
    )


def report_path(output_dir: Path, settings: ScreeningSettings, prompt: PromptTemplate) -> Path:
    """Name the report after the run's time, model and prompt version so runs can be compared."""
    stamp = datetime.now().strftime("%Y-%m-%dT%H%M")
    return output_dir / f"eval_{stamp}_{settings.model}_prompt-v{prompt.version}.xlsx"


async def run_evaluation(
    screening: ScreeningSettings,
    evaluation: EvaluationSettings,
    bq_client: bigquery.Client,
    runner: Runner,
    prompt: PromptTemplate,
    run_info: Mapping[str, object],
) -> tuple[Path, EvalMetrics]:
    """Screen every paper in the labels table and write the report; nothing goes to BigQuery.

    Returns:
        The report's path and the agreement metrics over papers that have a human label.
    """
    rows = query_rows(bq_client, _LABELLED_SQL.format(table=evaluation.labels_table))
    logger.info("Evaluating %s v%d on %d papers", prompt.name, prompt.version, len(rows))
    results = await gather_bounded(
        [paper_from_row(row) for row in rows],
        lambda paper: screen_paper(runner, prompt, paper),
        screening.max_concurrency,
    )

    predictions = []
    for row, result in zip(rows, results, strict=True):
        if isinstance(result, Exception) and not isinstance(result, _PER_PAPER_ERRORS):
            raise result
        if isinstance(result, Exception):
            logger.warning("Screening %s failed: %s", row["openalex_id"], result)
        predictions.append(build_prediction_row(row, result))

    metrics = compute_metrics(
        [(p.human_label, p.agent_label) for p in predictions if p.human_label is not None]
    )
    info = {
        **run_info,
        "Papers screened": len(predictions),
        "Labels table": evaluation.labels_table,
    }
    path = write_report(
        report_path(evaluation.output_dir, screening, prompt), info, metrics, predictions
    )
    return path, metrics

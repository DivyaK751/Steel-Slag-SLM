"""Run the screening agent on human-labelled papers and save an Excel report of the run."""

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

from google.adk.runners import Runner
from google.cloud import bigquery
from google.genai.errors import APIError

from steel_slag_slm.agents.relevance_screener import (
    DECISION_COLUMN_TYPES,
    paper_from_row,
    screen_paper,
)
from steel_slag_slm.agents.runner import TokenUsage, gather_bounded
from steel_slag_slm.config import EvaluationSettings, ModelPrice, ScreeningSettings
from steel_slag_slm.data.bigquery_store import merge_updates, query_rows
from steel_slag_slm.evaluation.cost import estimate_cost_usd
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
    row: Mapping[str, object],
    result: tuple[RelevanceDecision, TokenUsage] | Exception,
    price: ModelPrice | None,
) -> PredictionRow:
    """Line up one paper's human label with the agent's answer (or its failure)."""
    human = normalize_label(str(row.get("human_label") or ""))
    if isinstance(result, Exception):
        agent, decision, usage = FAILED, None, None
    else:
        decision, usage = result
        agent = decision.relevant
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
        input_tokens=usage.input_tokens if usage else None,
        output_tokens=usage.output_tokens if usage else None,
        cost_usd=estimate_cost_usd(usage, price) if usage else None,
    )


def cost_summary(
    predictions: Sequence[PredictionRow], price: ModelPrice | None
) -> dict[str, object]:
    """Token totals and estimated cost for the Summary sheet (failed papers report no tokens)."""
    usage = TokenUsage(
        sum(p.input_tokens or 0 for p in predictions),
        sum(p.output_tokens or 0 for p in predictions),
    )
    answered = sum(1 for p in predictions if p.input_tokens is not None)
    cost = estimate_cost_usd(usage, price)
    no_price = "n/a (add this model to pricing in base.yaml)"
    return {
        "Input tokens": usage.input_tokens,
        "Output tokens (incl. thinking)": usage.output_tokens,
        "Price used (USD per 1M in / out)": f"{price.input} / {price.output}"
        if price
        else no_price,
        "Estimated cost (USD)": f"{cost:.4f}" if cost is not None else no_price,
        "Estimated cost per paper (USD)": (
            f"{cost / answered:.5f}" if cost is not None and answered else "n/a"
        ),
    }


def decision_columns(prediction: PredictionRow) -> dict[str, object]:
    """The relevance columns for one paper, all empty when its screening failed."""
    failed = prediction.agent_label == FAILED
    return {
        "openalex_id": prediction.openalex_id,
        "relevant": None if failed else prediction.agent_label,
        "relevance_score": prediction.relevance_score,
        "relevance_confidence": prediction.relevance_confidence,
        "relevance_reason": None if failed else prediction.relevance_reason,
        "supporting_excerpt": prediction.supporting_excerpt,
    }


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
    write: bool = False,
) -> tuple[Path, EvalMetrics, int]:
    """Screen every paper in the labels table and write the Excel report.

    With write, the agent's answers also replace the relevance columns of the labels table
    (a failed paper's columns are cleared, so the table always shows one run).

    Returns:
        The report's path, the agreement metrics over papers that have a human label, and
        the number of BigQuery rows updated (0 without write).
    """
    rows = query_rows(bq_client, _LABELLED_SQL.format(table=evaluation.labels_table))
    logger.info("Evaluating %s v%d on %d papers", prompt.name, prompt.version, len(rows))
    results = await gather_bounded(
        [paper_from_row(row) for row in rows],
        lambda paper: screen_paper(runner, prompt, paper),
        screening.max_concurrency,
    )

    price = evaluation.prices.get(screening.model)
    predictions = []
    for row, result in zip(rows, results, strict=True):
        if isinstance(result, Exception) and not isinstance(result, _PER_PAPER_ERRORS):
            raise result
        if isinstance(result, Exception):
            logger.warning("Screening %s failed: %s", row["openalex_id"], result)
        predictions.append(build_prediction_row(row, result, price))

    metrics = compute_metrics(
        [(p.human_label, p.agent_label) for p in predictions if p.human_label is not None]
    )
    info = {
        **run_info,
        "Papers screened": len(predictions),
        "Labels table": evaluation.labels_table,
        **cost_summary(predictions, price),
    }
    path = write_report(
        report_path(evaluation.output_dir, screening, prompt), info, metrics, predictions
    )
    updated = 0
    if write:
        updated = merge_updates(
            bq_client,
            evaluation.labels_table,
            "openalex_id",
            [decision_columns(p) for p in predictions],
            DECISION_COLUMN_TYPES,
        )
    return path, metrics, updated

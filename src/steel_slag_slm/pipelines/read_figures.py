"""Read every extracted figure with Gemini, check each reading, and total the cost."""

import asyncio
import json
import logging
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from google.adk.runners import Runner
from google.genai.errors import APIError

from steel_slag_slm.agents.figure_reader import figure_label, read_figure
from steel_slag_slm.agents.runner import gather_bounded
from steel_slag_slm.config import FigureReadingSettings
from steel_slag_slm.evaluation.figure_checks import (
    FigureCheck,
    check_reading,
    figure_body_text,
)
from steel_slag_slm.exceptions import AgentOutputError
from steel_slag_slm.llm.costs import estimate_cost_usd
from steel_slag_slm.llm.prompts import PromptTemplate
from steel_slag_slm.pipelines.paper_selection import select_papers
from steel_slag_slm.schemas import FigureReading, FigureToRead, TokenUsage

logger = logging.getLogger(__name__)

FIGURES_FILE_NAME = "figures.json"
# Failures that cost one figure, not the run; the figure is reported as failed
_PER_FIGURE_ERRORS = (AgentOutputError, APIError, TimeoutError)


@dataclass(frozen=True, slots=True)
class FigureResult:
    """One figure's reading, its automatic checks and what it cost; reading is None if it failed."""

    figure: FigureToRead
    reading: FigureReading | None
    check: FigureCheck | None
    usage: TokenUsage
    cost_usd: float
    error: str | None = None


@dataclass(frozen=True, slots=True)
class CostSummary:
    """What a run cost, per figure and per paper, from successful readings."""

    papers: int
    figures_read: int
    figures_failed: int
    total_usd: float
    mean_per_figure_usd: float
    min_per_figure_usd: float
    max_per_figure_usd: float
    mean_per_paper_usd: float
    mean_per_type_usd: dict[str, float]  # keyed by the type Gemini gave the figure


def list_paper_dirs(
    figures_dir: Path, max_papers: int | None, paper_ids: list[str] | None = None
) -> list[Path]:
    """Paper folders written by extract-figures, in name order, at most max_papers of them.

    Raises:
        PapersNotFoundError: If paper_ids names a paper with no figures folder.
    """
    paper_dirs = sorted(path.parent for path in figures_dir.glob(f"*/{FIGURES_FILE_NAME}"))
    paper_dirs = select_papers(paper_dirs, paper_ids)
    return paper_dirs[:max_papers] if max_papers is not None else paper_dirs


def load_figures_to_read(paper_dir: Path) -> list[FigureToRead]:
    """Read one paper's figures.json into the records the agent needs."""
    records = json.loads((paper_dir / FIGURES_FILE_NAME).read_text())
    return [
        FigureToRead(
            paper_id=paper_dir.name,
            image_path=paper_dir / record["file"],
            figure_number=record["figure_number"],
            caption=record["caption"],
            pdf_text=record.get("pdf_text", ""),
            detected_type=record["figure_type"],
        )
        for record in records
    ]


def reading_path(figure: FigureToRead) -> Path:
    """Where a figure's reading is saved: beside its image, e.g. figure_03.png -> figure_03.json."""
    return figure.image_path.with_suffix(".json")


def _result_record(result: FigureResult) -> dict[str, Any]:
    return {
        "image": result.figure.image_path.name,
        "figure_number": result.figure.figure_number,
        "reading": result.reading.model_dump() if result.reading else None,
        "check": asdict(result.check) if result.check else None,
        "usage": asdict(result.usage),
        "cost_usd": round(result.cost_usd, 6),
        "error": result.error,
    }


async def _read_paper(
    paper_dir: Path, runner: Runner, prompt: PromptTemplate, settings: FigureReadingSettings
) -> list[FigureResult]:
    figures = load_figures_to_read(paper_dir)

    async def read_one(figure: FigureToRead) -> FigureResult:
        reading, usage = await asyncio.wait_for(
            read_figure(runner, prompt, figure, figure.image_path.read_bytes()),
            settings.call_timeout_s,
        )
        result = FigureResult(
            figure=figure,
            reading=reading,
            check=check_reading(reading, figure_body_text(figure.pdf_text, figure.caption)),
            usage=usage,
            cost_usd=estimate_cost_usd(usage, settings.prices),
        )
        logger.info(
            "%s %s: %s, $%.4f",
            figure.paper_id,
            figure_label(figure),
            reading.figure_type,
            result.cost_usd,
        )
        return result

    results: list[FigureResult] = []
    for figure, outcome in zip(
        figures, await gather_bounded(figures, read_one, settings.max_concurrency), strict=True
    ):
        if isinstance(outcome, _PER_FIGURE_ERRORS):
            # A timeout's message is empty, so name the error type too
            error = f"{type(outcome).__name__}: {outcome}".removesuffix(": ")
            logger.warning("%s %s failed: %s", figure.paper_id, figure_label(figure), error)
            outcome = FigureResult(figure, None, None, TokenUsage(), 0.0, error=error)
        elif isinstance(outcome, Exception):
            raise outcome
        results.append(outcome)

    for result in results:
        reading_path(result.figure).write_text(
            json.dumps(_result_record(result), indent=2, ensure_ascii=False)
        )
    return results


async def run_figure_reading(
    paper_dirs: list[Path],
    runner: Runner,
    prompt: PromptTemplate,
    settings: FigureReadingSettings,
) -> list[FigureResult]:
    """Read every figure of each paper, saving each reading as JSON beside the figure's image."""
    logger.info(
        "Reading figures with %s, prompt %s v%d", settings.model, prompt.name, prompt.version
    )
    results: list[FigureResult] = []
    for paper_dir in paper_dirs:
        results.extend(await _read_paper(paper_dir, runner, prompt, settings))
    return results


def summarize_costs(results: list[FigureResult]) -> CostSummary:
    """Total and average the cost of the figures that were read."""
    read = [result for result in results if result.reading is not None]
    costs = [result.cost_usd for result in read]
    per_paper: dict[str, float] = {}
    per_type: dict[str, list[float]] = {}
    for result in read:
        paper_id = result.figure.paper_id
        per_paper[paper_id] = per_paper.get(paper_id, 0.0) + result.cost_usd
        if result.reading is not None:
            per_type.setdefault(result.reading.figure_type, []).append(result.cost_usd)
    return CostSummary(
        papers=len({result.figure.paper_id for result in results}),
        figures_read=len(read),
        figures_failed=len(results) - len(read),
        total_usd=sum(costs),
        mean_per_figure_usd=statistics.fmean(costs) if costs else 0.0,
        min_per_figure_usd=min(costs, default=0.0),
        max_per_figure_usd=max(costs, default=0.0),
        mean_per_paper_usd=statistics.fmean(per_paper.values()) if per_paper else 0.0,
        mean_per_type_usd={kind: statistics.fmean(values) for kind, values in per_type.items()},
    )

"""Screen a paper's title, abstract and keywords for full-text extraction with Gemini."""

import re
from collections.abc import Mapping
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.runners import Runner

from steel_slag_slm.agents.runner import build_structured_agent, run_structured
from steel_slag_slm.config import ScreeningSettings
from steel_slag_slm.llm.prompts import PromptTemplate
from steel_slag_slm.schemas import PaperForScreening, RelevanceDecision

AGENT_NAME = "relevance_screener"
MISSING_FIELD = "(not available)"


def build_screener_agent(settings: ScreeningSettings, prompt: PromptTemplate) -> LlmAgent:
    """Create the screening agent from its settings and prompt."""
    return build_structured_agent(AGENT_NAME, settings, prompt.instruction, RelevanceDecision)


def paper_from_row(row: Mapping[str, Any]) -> PaperForScreening:
    """Build the screener's input from a BigQuery row.

    Keywords may be a list (papers table) or one comma-separated string (labelled copies).
    """
    keywords = row.get("keywords") or ()
    if isinstance(keywords, str):
        keywords = [keyword.strip() for keyword in keywords.split(",")]
    return PaperForScreening(
        openalex_id=row["openalex_id"],
        title=row.get("title") or "",
        abstract=row.get("abstract") or "",
        year=row.get("year"),
        keywords=tuple(keyword for keyword in keywords if keyword),
    )


def render_paper(prompt: PromptTemplate, paper: PaperForScreening) -> str:
    """Fill the prompt's input template with one paper, marking empty fields explicitly."""
    return prompt.render_input(
        openalex_id=paper.openalex_id,
        title=paper.title or MISSING_FIELD,
        year=paper.year or MISSING_FIELD,
        keywords=", ".join(paper.keywords) or MISSING_FIELD,
        abstract=paper.abstract or MISSING_FIELD,
    )


def _squash_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def check_excerpt(decision: RelevanceDecision, paper: PaperForScreening) -> RelevanceDecision:
    """Drop the supporting excerpt unless it really appears in the abstract (or title).

    The prompt asks for an exact copy, but models sometimes paraphrase; a made-up quote must
    never be stored as if it came from the paper.
    """
    excerpt = _squash_whitespace(decision.supporting_excerpt or "")
    source = _squash_whitespace(paper.abstract or paper.title)
    if excerpt and excerpt in source:
        return decision.model_copy(update={"supporting_excerpt": excerpt})
    return decision.model_copy(update={"supporting_excerpt": None})


async def screen_paper(
    runner: Runner, prompt: PromptTemplate, paper: PaperForScreening
) -> RelevanceDecision:
    """Ask the agent for a decision on one paper and verify its excerpt.

    Raises:
        AgentOutputError: If the agent's answer is missing or malformed.
    """
    decision = await run_structured(runner, render_paper(prompt, paper), RelevanceDecision)
    return check_excerpt(decision, paper)

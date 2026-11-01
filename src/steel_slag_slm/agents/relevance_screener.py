"""Screen a paper's title, abstract and keywords for full-text extraction with Gemini."""

import re
from collections.abc import Mapping
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.runners import Runner

from steel_slag_slm.agents.runner import TokenUsage, build_structured_agent, run_structured
from steel_slag_slm.config import ScreeningSettings
from steel_slag_slm.data.language_filter import non_english_reason
from steel_slag_slm.llm.prompts import PromptTemplate
from steel_slag_slm.schemas import PaperForScreening, RelevanceDecision

AGENT_NAME = "relevance_screener"
MISSING_FIELD = "(not available)"
NON_ENGLISH_PREFIX = "Not in English (checked before Gemini): "
# The BigQuery columns a screening decision fills, keyed by paper ID; the rest of a row is
# owned by the registry
DECISION_COLUMN_TYPES = {
    "openalex_id": "STRING",
    "relevant": "STRING",
    "relevance_score": "FLOAT64",
    "relevance_confidence": "STRING",
    "relevance_reason": "STRING",
    "supporting_excerpt": "STRING",
}


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


def non_english_decision(paper: PaperForScreening) -> RelevanceDecision | None:
    """A "No" for a paper that is clearly not in English, so it never reaches Gemini."""
    reason = non_english_reason(paper)
    if reason is None:
        return None
    return RelevanceDecision(
        relevant="No",
        relevance_score=0.0,
        relevance_confidence="High",
        relevance_reason=NON_ENGLISH_PREFIX + reason,
    )


async def screen_paper(
    runner: Runner, prompt: PromptTemplate, paper: PaperForScreening
) -> tuple[RelevanceDecision, TokenUsage]:
    """Ask the agent for a decision on one paper and verify its excerpt.

    Papers that are clearly not in English get "No" without calling Gemini.

    Returns:
        The decision and the tokens it cost (none for a non-English paper).

    Raises:
        AgentOutputError: If the agent's answer is missing or malformed.
    """
    skipped = non_english_decision(paper)
    if skipped is not None:
        return skipped, TokenUsage(0, 0)
    decision, usage = await run_structured(runner, render_paper(prompt, paper), RelevanceDecision)
    return check_excerpt(decision, paper), usage

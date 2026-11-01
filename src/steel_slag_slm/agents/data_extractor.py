"""Extract every workbook data value a parsed paper reports with Gemini."""

from google.adk.agents import LlmAgent
from google.adk.runners import Runner

from steel_slag_slm.agents.runner import (
    StructuredAnswer,
    build_structured_agent,
    run_structured_with_usage,
)
from steel_slag_slm.config import DataExtractionSettings
from steel_slag_slm.llm.prompts import PromptTemplate
from steel_slag_slm.schemas import PaperExtraction, ParsedPaper

AGENT_NAME = "data_extractor"
MISSING_FIELD = "(not available)"


def build_data_extractor_agent(
    settings: DataExtractionSettings, prompt: PromptTemplate
) -> LlmAgent:
    """Create the data-extraction agent from its settings and prompt."""
    return build_structured_agent(AGENT_NAME, settings, prompt.instruction, PaperExtraction)


async def extract_paper_data(
    runner: Runner, prompt: PromptTemplate, paper: ParsedPaper, title: str | None
) -> StructuredAnswer[PaperExtraction]:
    """Ask the agent for every value the paper's parsed Markdown reports, and its token usage.

    Raises:
        AgentOutputError: If the agent's answer is missing or malformed.
    """
    message = prompt.render_input(
        openalex_id=paper.openalex_id, title=title or MISSING_FIELD, markdown=paper.markdown
    )
    return await run_structured_with_usage(runner, message, PaperExtraction)

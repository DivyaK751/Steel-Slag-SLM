"""Read one extracted figure (plot, XRD pattern, process diagram or photo) with Gemini."""

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.genai import types

from steel_slag_slm.agents.runner import build_structured_agent, run_structured_with_usage
from steel_slag_slm.config import FigureReadingSettings
from steel_slag_slm.llm.prompts import PromptTemplate
from steel_slag_slm.schemas import FigureReading, FigureToRead, TokenUsage

AGENT_NAME = "figure_reader"
MISSING_FIELD = "(none)"


def build_figure_reader_agent(settings: FigureReadingSettings, prompt: PromptTemplate) -> LlmAgent:
    """Create the figure-reading agent from its settings and prompt."""
    return build_structured_agent(AGENT_NAME, settings, prompt.instruction, FigureReading)


def figure_label(figure: FigureToRead) -> str:
    """How the figure is named in messages and reports, e.g. "Fig. 3"."""
    return f"Fig. {figure.figure_number}" if figure.figure_number else "unnumbered figure"


async def read_figure(
    runner: Runner, prompt: PromptTemplate, figure: FigureToRead, png: bytes
) -> tuple[FigureReading, TokenUsage]:
    """Ask the agent what the attached figure shows, and how many tokens that took.

    Raises:
        AgentOutputError: If the agent's answer is missing or malformed.
    """
    message = prompt.render_input(
        paper_id=figure.paper_id,
        figure_label=figure_label(figure),
        caption=figure.caption or MISSING_FIELD,
        pdf_text=figure.pdf_text or MISSING_FIELD,
    )
    image = types.Part.from_bytes(data=png, mime_type="image/png")
    return await run_structured_with_usage(runner, message, FigureReading, attachments=[image])

"""Copy a paper's abstract from the first pages of its PDF with Gemini, then verify it."""

import re
from dataclasses import dataclass

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.genai import types

from steel_slag_slm.agents.runner import build_structured_agent, run_structured
from steel_slag_slm.config import AbstractExtractionSettings
from steel_slag_slm.llm.prompts import PromptTemplate
from steel_slag_slm.schemas import ExtractedAbstract, PaperForAbstract

AGENT_NAME = "abstract_extractor"
MISSING_FIELD = "(not available)"
# Fewer characters than this means the pages are scanned images with no text to check against
MIN_TEXT_LAYER_CHARS = 200
# The abstract counts as found in the pages when this share of its 8-word runs appears there
MATCH_WORDS = 8
MIN_MATCH_SHARE = 0.9


@dataclass(frozen=True, slots=True)
class AbstractOutcome:
    """What to store for one paper: abstract_source, the abstract (if any), and how it matched."""

    source: str  # pdf, pdf_unverified, pdf_mismatch or not_found
    abstract: str | None
    match_share: float | None


def build_extractor_agent(settings: AbstractExtractionSettings, prompt: PromptTemplate) -> LlmAgent:
    """Create the abstract-extraction agent from its settings and prompt."""
    return build_structured_agent(AGENT_NAME, settings, prompt.instruction, ExtractedAbstract)


async def extract_abstract(
    runner: Runner, prompt: PromptTemplate, paper: PaperForAbstract, pages_pdf: bytes
) -> ExtractedAbstract:
    """Ask the agent for the abstract printed on the attached first pages.

    Raises:
        AgentOutputError: If the agent's answer is missing or malformed.
    """
    message = prompt.render_input(openalex_id=paper.openalex_id, title=paper.title or MISSING_FIELD)
    pdf_part = types.Part.from_bytes(data=pages_pdf, mime_type="application/pdf")
    return await run_structured(runner, message, ExtractedAbstract, attachments=[pdf_part])


def _words(text: str) -> list[str]:
    # Undo end-of-line hyphenation first, as the prompt asks the model to do
    text = re.sub(r"-\s*\n\s*", "", text)
    return re.findall(r"[^\W_]+", text.lower())


def match_share(abstract: str, text: str) -> float:
    """Share of the abstract's 8-word runs found in text, ignoring case and punctuation."""
    abstract_words = _words(abstract)
    joined_text = " ".join(_words(text))
    if len(abstract_words) < MATCH_WORDS:
        return 1.0 if " ".join(abstract_words) in joined_text else 0.0
    runs = [
        " ".join(abstract_words[start : start + MATCH_WORDS])
        for start in range(len(abstract_words) - MATCH_WORDS + 1)
    ]
    return sum(run in joined_text for run in runs) / len(runs)


def judge_abstract(result: ExtractedAbstract, pages_text: str) -> AbstractOutcome:
    """Decide what to store: only abstracts that really appear on the pages are trusted.

    The model is told to copy, not write, but a made-up abstract must never be stored as if
    it came from the paper. Scanned pages can't be checked, so those are flagged instead.
    """
    abstract = re.sub(r"\s+", " ", result.abstract or "").strip()
    if not result.found or not abstract:
        return AbstractOutcome("not_found", None, None)
    if len(pages_text.strip()) < MIN_TEXT_LAYER_CHARS:
        return AbstractOutcome("pdf_unverified", abstract, None)
    share = match_share(abstract, pages_text)
    if share >= MIN_MATCH_SHARE:
        return AbstractOutcome("pdf", abstract, share)
    return AbstractOutcome("pdf_mismatch", None, share)

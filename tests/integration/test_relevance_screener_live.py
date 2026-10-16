"""Calls real Gemini on Vertex AI; run with `uv run pytest -m integration`."""

import asyncio

import pytest

from steel_slag_slm.agents.relevance_screener import build_screener_agent, screen_paper
from steel_slag_slm.agents.runner import build_runner
from steel_slag_slm.config import ScreeningSettings, load_gcp_settings
from steel_slag_slm.llm.prompts import load_prompt
from steel_slag_slm.schemas import PaperForScreening

pytestmark = pytest.mark.integration

# Short made-up abstracts with an unambiguous answer under the prompt's rules
CASES = [
    (
        "Yes",
        "Hydration of BOF steel slag as a partial cement replacement",
        "Basic oxygen furnace (BOF) slag was ground and used to replace 10-30% of Portland "
        "cement in mortar. Compressive strength and hydration products were measured.",
    ),
    (
        "No",
        "Steel slag aggregate in asphalt surface layers",
        "Coarse steel slag aggregate replaced natural basalt in asphalt mixtures. Rutting "
        "resistance and skid resistance were compared over two years of traffic.",
    ),
    (
        "No",
        "Strength of GGBFS concrete",
        "Ground granulated blast furnace slag replaced 50% of cement in concrete, and the "
        "compressive strength was tested at 28 and 90 days.",
    ),
]


@pytest.mark.parametrize(("expected", "title", "abstract"), CASES)
def test_screener_matches_obvious_cases(expected: str, title: str, abstract: str) -> None:
    load_gcp_settings()
    settings = ScreeningSettings(
        model="gemini-3.8-flash",
        temperature=0.0,
        prompt_name="screen_relevance",
        batch_size=1,
        max_concurrency=1,
        papers_table="unused.unused.unused",
    )
    prompt = load_prompt("screen_relevance")
    runner = build_runner(build_screener_agent(settings, prompt))
    paper = PaperForScreening("W0", title, abstract, 2024, ())
    decision = asyncio.run(screen_paper(runner, prompt, paper))
    assert decision.relevant == expected
    assert decision.supporting_excerpt is not None

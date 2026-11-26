"""Writes a short answer with every chat-time model through the real LiteLLM gateway.

Run with `uv run pytest -m integration tests/integration/test_answer_writer_live.py`, with
LLM_GATEWAY_URL and LLM_GATEWAY_KEY in .env. Each model must cite only labels in the evidence.
"""

import pytest
import yaml

from steel_slag_slm.agents.answer_writer import unknown_citations, write_answer
from steel_slag_slm.config import BASE_CONFIG_PATH, load_answer_settings, load_llm_gateway_settings
from steel_slag_slm.llm.gateway import GatewayClient
from steel_slag_slm.llm.prompts import load_prompt

pytestmark = pytest.mark.integration

# Read without .env, so collecting this file never needs the gateway's secrets
MODELS = list(yaml.safe_load(BASE_CONFIG_PATH.read_text())["llm_gateway"]["models"])
QUESTION = "What 28-day compressive strength does concrete with 20% BOF slag reach?"
EVIDENCE = """# Question
What 28-day compressive strength does concrete with 20% BOF slag reach?

## Similar slags
[S1] W123 MAT-0001 (BOF): Strength of slag concrete (2019). Wang, Li. 10.1/abc

## Compressive strength
- [S1 EXP-0004 / Mix 20%] strength_28d: 42.5 MPa; curing: water, 20 C (p. 6)

## Paper passages
[P1] Strength of slag concrete, Results (W123, text)
Replacing 20% of the cement with BOF slag lowered early strength, but by 28 days the
mixes reached strength similar to the control."""


@pytest.mark.parametrize("model", MODELS)
def test_model_answers_citing_only_labels_in_the_evidence(model: str) -> None:
    settings = load_answer_settings()
    client = GatewayClient(load_llm_gateway_settings(), model)

    answer = "".join(
        write_answer(QUESTION, EVIDENCE, client, load_prompt(settings.prompt_name), settings)
    )

    assert "42.5" in answer
    assert "[S1]" in answer
    assert unknown_citations(answer, EVIDENCE) == []
    assert "Confidence:" in answer

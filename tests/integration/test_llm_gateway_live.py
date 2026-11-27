"""Calls every chat-time model through the real LiteLLM gateway.

Run with `uv run pytest -m integration tests/integration/test_llm_gateway_live.py`, with
LLM_GATEWAY_URL and LLM_GATEWAY_KEY in .env. It answers the two risks of docs/adr/005: does each
model return JSON that follows a schema, and does Qwen answer without a <think> block?
"""

from typing import Literal

import pytest
import yaml
from pydantic import BaseModel

from steel_slag_slm.config import BASE_CONFIG_PATH, LlmGatewaySettings, load_llm_gateway_settings
from steel_slag_slm.llm.gateway import GatewayClient

pytestmark = pytest.mark.integration

# Read without .env, so collecting this file never needs the gateway's secrets
MODELS = list(yaml.safe_load(BASE_CONFIG_PATH.read_text())["llm_gateway"]["models"])


class _Filter(BaseModel):
    column: str
    op: Literal["=", "<", ">"]
    value: float | str


class _Form(BaseModel):
    """Shaped like the question reader's form: nested objects, a fixed choice and a union."""

    message_type: Literal["question", "chat", "off_topic"]
    filters: list[_Filter]
    search_query: str


@pytest.fixture(scope="module")
def settings() -> LlmGatewaySettings:
    return load_llm_gateway_settings()


@pytest.mark.parametrize("model", MODELS)
def test_model_returns_json_that_follows_the_schema(
    settings: LlmGatewaySettings, model: str
) -> None:
    client = GatewayClient(settings, model)
    answer = client.complete_json(
        "Read the user's message into the form. Use the column concrete.slag_type.",
        "What is the 28-day strength of concrete made with BOF slag?",
        _Form.model_json_schema(),
    )

    form = _Form.model_validate(answer)
    assert form.message_type == "question"
    assert form.filters


@pytest.mark.parametrize("model", MODELS)
def test_model_answers_without_a_think_block(settings: LlmGatewaySettings, model: str) -> None:
    # Long enough to arrive in several pieces; no max_tokens, since thinking models spend
    # part of it reasoning and a small cap can leave no answer at all
    pieces = list(
        GatewayClient(settings, model).stream_text(
            "You are a concise assistant.", "Explain steel slag in about 150 words."
        )
    )

    text = "".join(piece.text for piece in pieces)
    assert text.strip()
    assert "<think>" not in text
    assert not any(piece.is_thinking for piece in pieces)  # thinking is off by default
    assert len(pieces) > 1  # streamed, not sent as one block

from typing import Any

from steel_slag_slm.agents.question_form import form_columns
from steel_slag_slm.agents.router import RouteTools
from steel_slag_slm.config import QuestionRoutingSettings
from steel_slag_slm.llm.prompts import load_prompt
from steel_slag_slm.pipelines.prepare_evidence import prepare_evidence
from steel_slag_slm.rag.search import SearchHit
from steel_slag_slm.rag.slag_lookup import SlagLookup
from steel_slag_slm.schemas import Chunk

SETTINGS = QuestionRoutingSettings(
    prompt_name="read_question",
    examples_name="read_question_examples", examples_k=6,
    max_listed_values=12, search_top_k=15, evidence_max_chars=9000,
)  # fmt: skip
HIT = SearchHit(0.9, Chunk("W7", "text", "Results", "Strength at 14 days was 31 MPa.", 0))


class FixedModel:
    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        return self.answer


def _tools(searched: list[str]) -> RouteTools:
    def search(query: str, top_k: int) -> list[SearchHit]:
        searched.append(query)
        return [
            SearchHit(0.9, Chunk("W7", "text", "Discussion", "Free lime hydrates to Ca(OH)2.", 0))
        ]

    def unused(*_: Any) -> Any:
        raise AssertionError("the tables should not be read for a why question")

    return RouteTools(unused, lambda: [], lambda _: SlagLookup([], [], []), search)


def test_a_why_question_is_read_routed_to_paper_search_and_packaged():
    searched: list[str] = []
    model = FixedModel({
        "message_type": "question",
        "search_query": "why free lime causes expansion in steel slag",
    })  # fmt: skip

    prepared = prepare_evidence(
        "Why does free lime make slag expand?", model, load_prompt("read_question"),
        form_columns(), _tools(searched), SETTINGS,
    )  # fmt: skip

    assert [call.tool for call in prepared.plan.calls] == ["paper_search"]
    assert searched == ["why free lime causes expansion in steel slag"]
    assert "[P1]" in prepared.evidence and "Free lime hydrates" in prepared.evidence


def test_an_unreadable_question_is_searched_as_typed():
    searched: list[str] = []
    model = FixedModel({"message_type": "a poem"})  # invalid on both tries

    prepared = prepare_evidence(
        "Why does slag expand?", model, load_prompt("read_question"), form_columns(),
        _tools(searched), SETTINGS,
    )  # fmt: skip

    assert searched == ["Why does slag expand?"]
    assert "could not be read" in prepared.evidence


def test_an_age_without_a_column_uses_the_nearest_and_searches_the_papers():
    searched: list[str] = []
    model = FixedModel({
        "message_type": "question", "values_only": True,
        "properties": ["concrete_performance.compressive_strength_28d_mpa"],
        "search_query": "14-day strength of steel slag concrete",
    })  # fmt: skip
    tools = RouteTools(
        lambda _: None, lambda: [], lambda _: SlagLookup([], [], []),
        lambda query, top_k: searched.append(query) or [HIT],
    )  # fmt: skip

    prepared = prepare_evidence(
        "What 14-day strength?", model, load_prompt("read_question"), form_columns(), tools,
        SETTINGS,
    )  # fmt: skip

    assert prepared.form.properties == ["concrete_performance.compressive_strength_7d_mpa"]
    assert [call.tool for call in prepared.plan.calls] == ["lookup", "paper_search"]
    assert searched == ["14-day strength of steel slag concrete"]
    assert "No 14-day column" in prepared.evidence

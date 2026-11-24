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
    reader_model="m", reader_url="http://x", reader_timeout_s=1, prompt_name="read_question",
    search_top_k=15, evidence_max_chars=9000,
)  # fmt: skip


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
        "message_type": "question", "intents": ["explanation"],
        "search_query": "why free lime causes expansion in steel slag",
    })  # fmt: skip

    prepared = prepare_evidence(
        "Why does free lime make slag expand?", model, load_prompt("read_question"),
        form_columns(), _tools(searched), SETTINGS,
    )  # fmt: skip

    assert [call.tool for call in prepared.plan.calls] == ["paper_search"]
    assert searched == ["why free lime causes expansion in steel slag"]
    assert "[P1]" in prepared.evidence and "Free lime hydrates" in prepared.evidence

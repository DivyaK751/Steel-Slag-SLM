from collections.abc import Iterator
from typing import Any

from steel_slag_slm.agents.question_form import ColumnFilter, QuestionForm, form_columns
from steel_slag_slm.agents.router import RouteTools
from steel_slag_slm.config import AnswerSettings, QuestionRoutingSettings
from steel_slag_slm.llm.prompts import load_prompt
from steel_slag_slm.pipelines.assistant_setup import Assistant
from steel_slag_slm.rag.search import SearchHit
from steel_slag_slm.rag.slag_lookup import SlagLookup
from steel_slag_slm.schemas import Chunk

ROUTING = QuestionRoutingSettings(
    prompt_name="read_question", examples_name="read_question_examples", examples_k=6,
    max_listed_values=12, search_top_k=15, evidence_max_chars=9000,
)  # fmt: skip
SLAG_TYPE = "concrete_performance.steel_slag_type"
REPLACEMENT = "concrete_performance.scm_replacement_pct"


class FixedReader:
    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        return self.answer


class UnusedWriter:
    def stream_text(self, *_: Any, **__: Any) -> Iterator[str]:
        raise AssertionError("prepare must not write an answer")


def _assistant(reader_answer: dict[str, Any], searched: list[str]) -> Assistant:
    def search(query: str, top_k: int) -> list[SearchHit]:
        searched.append(query)
        return [SearchHit(0.9, Chunk("W7", "text", "Results", "28-day strength was 41 MPa.", 0))]

    tools = RouteTools(lambda _: None, lambda: [], lambda _: SlagLookup([], [], []), search)  # type: ignore[arg-type,return-value]
    return Assistant(
        reader=FixedReader(reader_answer),
        read_prompt=load_prompt("read_question"),
        columns=form_columns(),
        tools=tools,
        routing=ROUTING,
        examples=None,
        writer=UnusedWriter(),
        answer_prompt=load_prompt("answer_question"),
        answer_settings=AnswerSettings(prompt_name="answer_question", temperature=0.2),
        paper_citations=lambda _: {},
    )


def test_prepare_reads_routes_and_packages_the_message():
    searched: list[str] = []
    assistant = _assistant(
        {"message_type": "question", "search_query": "steel slag concrete strength"}, searched
    )

    prepared = assistant.prepare("How strong is slag concrete?")

    assert searched == ["steel slag concrete strength"]
    assert "[P1]" in prepared.evidence and "41 MPa" in prepared.evidence


def test_prepare_fills_a_follow_up_from_the_previous_form():
    previous = QuestionForm(
        message_type="question",
        filters=[
            ColumnFilter(column=SLAG_TYPE, op="=", value="BOF"),
            ColumnFilter(column=REPLACEMENT, op="=", value=20.0),
        ],
        search_query="28-day strength of concrete with 20% BOF slag",
    )
    follow_up = {
        "message_type": "follow_up",
        "filters": [{"column": SLAG_TYPE, "op": "=", "value": "EAF"}],
    }

    form = _assistant(follow_up, []).prepare("and for EAF?", previous=previous).form

    assert form.message_type == "question"
    assert {(f.column, f.value) for f in form.filters} == {(REPLACEMENT, 20.0), (SLAG_TYPE, "EAF")}

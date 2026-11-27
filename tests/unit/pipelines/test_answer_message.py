from collections.abc import Iterator, Sequence
from typing import Any

import pytest

from steel_slag_slm.agents.question_form import QuestionForm
from steel_slag_slm.agents.router import RoutePlan, ToolCall, ToolResults
from steel_slag_slm.config import AnswerSettings
from steel_slag_slm.llm.gateway import ReplyPiece
from steel_slag_slm.llm.prompts import load_prompt
from steel_slag_slm.pipelines.answer_message import (
    BLANK_MESSAGE,
    SEARCHING,
    WRITING,
    ChatSession,
    answer_message,
)
from steel_slag_slm.pipelines.assistant_setup import ModelChoice
from steel_slag_slm.pipelines.prepare_evidence import PreparedEvidence
from steel_slag_slm.rag.search import SearchHit
from steel_slag_slm.rag.similar_slags import SlagProfile
from steel_slag_slm.schemas import ChatEvent, Chunk, PaperCitation

QUESTION = QuestionForm(message_type="question", search_query="free lime expansion")
SEARCH = RoutePlan((ToolCall("paper_search"),), False, None, ())
REPLY_ONLY = RoutePlan((), True, None, ())
PASSAGE = SearchHit(0.9, Chunk("W9", "text", "Results", "Free lime expands.", 0, pages=(4,)))
EVIDENCE = "# Question\nWhy?\n\n## Paper passages\n[P1] Results (W9, text)\nFree lime expands."


class FakeWriter:
    def __init__(self, pieces: list[str | ReplyPiece]) -> None:
        self.pieces = pieces

    def stream_text(self, *_: Any, **__: Any) -> Iterator[ReplyPiece]:
        for piece in self.pieces:
            yield piece if isinstance(piece, ReplyPiece) else ReplyPiece(piece)


class FakeAssistant:
    """The parts of Assistant that answer_message uses, with a canned prepare()."""

    def __init__(self, form: QuestionForm, plan: RoutePlan, pieces: list[str | ReplyPiece]) -> None:
        self.prepared = PreparedEvidence(form, plan, ToolResults(passages=[PASSAGE]), EVIDENCE)
        self.writer = FakeWriter(pieces)
        self.answer_prompt = load_prompt("answer_question")
        self.answer_settings = AnswerSettings(prompt_name="answer_question", temperature=0.2)
        self.calls: list[tuple[str, SlagProfile | None, QuestionForm | None]] = []
        self.choices: list[ModelChoice] = []

    def with_models(self, choice: ModelChoice) -> "FakeAssistant":
        self.choices.append(choice)
        self.writer = FakeWriter([f"Written by {choice.answer_model}."])
        return self

    def prepare(
        self, message: str, profile: SlagProfile | None, previous: QuestionForm | None
    ) -> PreparedEvidence:
        self.calls.append((message, profile, previous))
        return self.prepared

    def paper_citations(self, paper_ids: Sequence[str]) -> dict[str, PaperCitation]:
        return {"W9": PaperCitation("Free lime in slag", ("A. Author",), 2021, None)}


def _events(
    assistant: FakeAssistant,
    message: str = "Why?",
    session: ChatSession | None = None,
    choice: ModelChoice | None = None,
):
    return list(answer_message(message, session or ChatSession(), assistant, choice))  # type: ignore[arg-type]


def test_a_question_streams_status_tokens_citations_then_done():
    assistant = FakeAssistant(QUESTION, SEARCH, ["It hydrates ", "and swells [P1]."])

    events = _events(assistant)

    assert [(e.type, e.text) for e in events[:2]] == [("status", SEARCHING), ("status", WRITING)]
    assert "".join(e.text for e in events if e.type == "token") == "It hydrates and swells [P1]."
    citations = events[-2]
    assert citations.type == "citations"
    assert [(c.label, c.title, c.pages) for c in citations.citations] == [
        ("P1", "Free lime in slag", [4])
    ]
    assert events[-1] == ChatEvent(type="done")


def test_reasoning_streams_as_thinking_events_and_is_not_part_of_the_answer():
    thinking = ReplyPiece("They ask about [P4]...", is_thinking=True)
    assistant = FakeAssistant(QUESTION, SEARCH, [thinking, "It swells [P1]."])

    events = _events(assistant)

    assert [e.type for e in events[2:]] == ["thinking", "token", "citations", "done"]
    assert events[2].text == "They ask about [P4]..."
    # Only the answer is cited: P4 in the reasoning gets no citation
    assert [c.label for c in events[-2].citations] == ["P1"]


def test_the_question_form_is_kept_and_passed_to_the_next_message():
    assistant = FakeAssistant(QUESTION, SEARCH, ["ok"])
    profile = SlagProfile(steel_slag_type="EAF", oxides={})
    session = ChatSession(profile=profile)

    _events(assistant, "Why?", session)
    _events(assistant, "and for BOF?", session)

    assert session.previous == QUESTION
    assert assistant.calls == [("Why?", profile, None), ("and for BOF?", profile, QUESTION)]


@pytest.mark.parametrize("message_type", ["chat", "off_topic"])
def test_small_talk_skips_writing_status_and_keeps_the_earlier_question(message_type):
    assistant = FakeAssistant(QuestionForm(message_type=message_type), REPLY_ONLY, ["Hello!"])
    session = ChatSession(previous=QUESTION)

    events = _events(assistant, "hi", session)

    assert [e.type for e in events] == ["status", "token", "done"]
    assert session.previous == QUESTION


def test_a_made_up_label_gets_no_citation_and_is_logged(caplog):
    events = _events(FakeAssistant(QUESTION, SEARCH, ["Swells [P4]."]))

    assert [e.type for e in events][-2:] == ["token", "done"]
    assert "P4" in caplog.text


def test_a_blank_message_yields_one_error_event():
    assistant = FakeAssistant(QUESTION, SEARCH, ["unused"])

    assert _events(assistant, "   ") == [ChatEvent(type="error", text=BLANK_MESSAGE)]
    assert assistant.calls == []


def test_a_model_choice_answers_with_the_chosen_models():
    assistant = FakeAssistant(QUESTION, SEARCH, ["Default model."])
    choice = ModelChoice("qwen3-8b", "qwen3-8b", thinking=True)

    events = _events(assistant, choice=choice)

    assert assistant.choices == [choice]
    assert "".join(e.text for e in events if e.type == "token") == "Written by qwen3-8b."


def test_no_model_choice_keeps_the_assistant_models():
    assistant = FakeAssistant(QUESTION, SEARCH, ["Default model."])

    _events(assistant)

    assert assistant.choices == []

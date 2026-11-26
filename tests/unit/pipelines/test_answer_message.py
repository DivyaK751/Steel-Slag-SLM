import re

from steel_slag_slm.pipelines.answer_message import BLANK_MESSAGE, ChatSession, answer_message
from steel_slag_slm.rag.similar_slags import SlagProfile
from steel_slag_slm.schemas import ChatEvent


def _answer_text(events: list[ChatEvent]) -> str:
    return "".join(event.text for event in events if event.type == "token")


def test_answer_message_follows_the_event_order_of_adr_005() -> None:
    events = list(answer_message("What is BOF slag used for?", ChatSession()))
    types = [event.type for event in events]

    assert types[0] == "status"
    assert types[-1] == "done"
    assert types.count("done") == 1
    assert types.count("citations") <= 1
    assert "error" not in types
    # status steps, then tokens, then citations: each kind appears in one unbroken run
    order = {"status": 0, "token": 1, "citations": 2, "done": 3}
    assert [order[kind] for kind in types] == sorted(order[kind] for kind in types)


def test_answer_message_has_a_citation_for_every_cited_label() -> None:
    events = list(answer_message("What is BOF slag used for?", ChatSession()))
    cited = set(re.findall(r"\[([SP]\d+)\]", _answer_text(events)))
    (citations_event,) = [event for event in events if event.type == "citations"]

    assert cited
    assert cited == {citation.label for citation in citations_event.citations}


def test_answer_message_rejects_a_blank_message_with_one_error_event() -> None:
    events = list(answer_message("   ", ChatSession()))

    assert events == [ChatEvent(type="error", text=BLANK_MESSAGE)]


def test_answer_message_names_the_profiled_slag_type() -> None:
    session = ChatSession(profile=SlagProfile(steel_slag_type="Ladle", oxides={}))

    assert "Ladle" in _answer_text(list(answer_message("Is it safe?", session)))

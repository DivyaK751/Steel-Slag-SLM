"""Answer one chat message as a stream of events (contract: docs/adr/005-chat-interface.md).

message -> reader form -> tools -> cited evidence -> streamed answer -> citations.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass

from steel_slag_slm.agents.answer_citations import build_citations
from steel_slag_slm.agents.answer_writer import unknown_citations, write_answer
from steel_slag_slm.agents.question_form import QuestionForm
from steel_slag_slm.pipelines.assistant_setup import Assistant, ModelChoice
from steel_slag_slm.rag.similar_slags import SlagProfile
from steel_slag_slm.schemas import ChatEvent

logger = logging.getLogger(__name__)

BLANK_MESSAGE = "Please type a question about steel slag."
SEARCHING = "Reading your question and searching the papers and tables…"
WRITING = "Writing the answer…"


@dataclass(slots=True)  # mutable: the backend sets the profile, each question sets previous
class ChatSession:
    """One user's conversation: their slag profile, and the last question's form."""

    profile: SlagProfile | None = None
    previous: QuestionForm | None = None  # so a follow-up ("and for BOF?") can build on it


def answer_message(
    message: str,
    session: ChatSession,
    assistant: Assistant,
    choice: ModelChoice | None = None,
) -> Iterator[ChatEvent]:
    """Stream the events that answer one message, and remember its form for follow-ups.

    choice swaps the reader and writer models for this message; None keeps the assistant's.
    A blank message yields one "error" event. Failures (gateway, BigQuery) propagate; the
    chat API turns them into the stream's closing "error" event.
    """
    if not message.strip():
        yield ChatEvent(type="error", text=BLANK_MESSAGE)
        return
    if choice:
        assistant = assistant.with_models(choice)
    yield ChatEvent(type="status", text=SEARCHING)
    prepared = assistant.prepare(message, session.profile, session.previous)
    if prepared.form.message_type == "question":  # small talk keeps the earlier question
        session.previous = prepared.form
    if not prepared.plan.reply_only:
        yield ChatEvent(type="status", text=WRITING)

    pieces = []  # the answer only: reasoning is shown but never cited
    for piece in write_answer(
        message,
        prepared.evidence,
        assistant.writer,
        assistant.answer_prompt,
        assistant.answer_settings,
    ):
        if piece.is_thinking:
            yield ChatEvent(type="thinking", text=piece.text)
            continue
        pieces.append(piece.text)
        yield ChatEvent(type="token", text=piece.text)

    answer = "".join(pieces)
    unknown = unknown_citations(answer, prepared.evidence)
    if unknown:
        logger.warning("The answer cites labels the evidence lacks: %s", ", ".join(unknown))
    citations = build_citations(
        answer, prepared.evidence, prepared.results, assistant.paper_citations
    )
    if citations:
        yield ChatEvent(type="citations", citations=citations)
    yield ChatEvent(type="done")

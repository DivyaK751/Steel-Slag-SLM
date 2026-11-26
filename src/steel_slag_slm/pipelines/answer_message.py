"""Answer one chat message as a stream of events (contract: docs/adr/005-chat-interface.md).

STUB: until question routing and the answer model are connected (A3), answer_message yields
fixed events in the contract's order, so the chat API and UI can be built against it.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass

from steel_slag_slm.rag.similar_slags import SlagProfile
from steel_slag_slm.schemas import ChatEvent, Citation

BLANK_MESSAGE = "Please type a question about steel slag."

# Fixed stub output, replaced by the real answer path in A3
_STUB_STEPS = ("Searching papers…", "Writing the answer…")
_STUB_ANSWER = (
    "This is a placeholder answer about {slag}. Steel slag can replace part of the cement in "
    "concrete [P1], and slags close to yours in chemistry are reported in the tables [S1]."
)
_STUB_CITATIONS = (
    Citation(
        label="P1",
        paper_id="stub-paper-1",
        title="Placeholder paper on steel slag in concrete",
        authors=["A. Author", "B. Author"],
        year=2024,
        doi="10.0000/stub.1",
        pages=[3],
    ),
    Citation(label="S1", paper_id="stub-paper-2", title="Placeholder slag catalogue entry"),
)


@dataclass(slots=True)  # mutable: the backend sets the profile, and A3 adds the previous form
class ChatSession:
    """One user's conversation: their slag profile, if they gave one."""

    profile: SlagProfile | None = None


def _split_tokens(text: str) -> list[str]:
    """Split text into words, each keeping its trailing space, so the pieces join back exactly."""
    return re.findall(r"\S+\s*", text)


def answer_message(message: str, session: ChatSession) -> Iterator[ChatEvent]:
    """Stream the events that answer one message. STUB until A3: the events are fixed.

    A blank message yields one "error" event.
    """
    if not message.strip():
        yield ChatEvent(type="error", text=BLANK_MESSAGE)
        return
    for step in _STUB_STEPS:
        yield ChatEvent(type="status", text=step)
    slag = f"your {session.profile.steel_slag_type} slag" if session.profile else "steel slag"
    for token in _split_tokens(_STUB_ANSWER.format(slag=slag)):
        yield ChatEvent(type="token", text=token)
    yield ChatEvent(type="citations", citations=list(_STUB_CITATIONS))
    yield ChatEvent(type="done")

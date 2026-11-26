"""In-memory chat sessions, keyed by the id the browser makes for each chat."""

from uuid import UUID

from steel_slag_slm.pipelines.answer_message import ChatSession
from steel_slag_slm.rag.similar_slags import SlagProfile


class SessionStore:
    """Hold every open chat's session; all are lost when the server restarts (ADR 005)."""

    def __init__(self) -> None:
        self._sessions: dict[UUID, ChatSession] = {}

    def get_or_create(self, session_id: UUID) -> ChatSession:
        """Return the session for this id, starting an empty one the first time it is seen."""
        return self._sessions.setdefault(session_id, ChatSession())

    def set_profile(self, session_id: UUID, profile: SlagProfile) -> None:
        """Save the user's slag profile, replacing any earlier one."""
        self.get_or_create(session_id).profile = profile

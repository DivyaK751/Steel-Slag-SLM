"""The chat API: routes only, streaming answer_message as server-sent events (ADR 005).

Run with: uv run uvicorn backend.app:app --reload --port 8765
"""

import logging
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from functools import partial
from typing import get_args
from uuid import UUID

from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.sessions import SessionStore
from steel_slag_slm.data.slag_catalogue import OXIDE_LABELS
from steel_slag_slm.pipelines.answer_message import ChatSession, answer_message
from steel_slag_slm.pipelines.assistant_setup import build_assistant
from steel_slag_slm.rag.similar_slags import SlagProfile
from steel_slag_slm.schemas import ChatEvent, SteelSlagType

logger = logging.getLogger(__name__)

AnswerFn = Callable[[str, ChatSession], Iterator[ChatEvent]]

SERVER_ERROR_MESSAGE = "Something went wrong while answering. Please try again."
# no-transform/X-Accel-Buffering stop proxies from holding events back until the stream ends
SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}


class ChatRequest(BaseModel):
    """One message the user sends in a chat."""

    session_id: UUID
    message: str


class OxideOption(BaseModel):
    """One oxide the profile form offers: its catalogue column and the name shown."""

    column: str
    label: str


class ProfileOptions(BaseModel):
    """The choices the profile form offers, from the same lists the backend validates with."""

    slag_types: list[str]
    oxides: list[OxideOption]


def format_sse(event: ChatEvent) -> str:
    """Write one event in the server-sent events format."""
    return f"data: {event.model_dump_json()}\n\n"


def stream_answer(answer: AnswerFn, message: str, session: ChatSession) -> Iterator[str]:
    """Stream the answer's events, ending with one "error" event if answering fails midway."""
    try:
        for event in answer(message, session):
            yield format_sse(event)
    except Exception:
        # The stream must still end with "done" or "error" (ADR 005), so turn any failure into one
        logger.exception("Answering failed")
        yield format_sse(ChatEvent(type="error", text=SERVER_ERROR_MESSAGE))


def create_app(answer: AnswerFn | None = None) -> FastAPI:
    """Build the API. Without an answer function (tests pass a fake), the real assistant is
    built at startup: the search index, models and BigQuery tools, once for every chat."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.sessions = SessionStore()
        # Slow (about a minute): loads the index from Drive and the embedding models
        app.state.answer = answer or partial(answer_message, assistant=build_assistant())
        yield

    app = FastAPI(title="Steel slag chat", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/profile/options")
    def profile_options() -> ProfileOptions:
        oxides = [OxideOption(column=col, label=label) for col, label in OXIDE_LABELS.items()]
        return ProfileOptions(slag_types=list(get_args(SteelSlagType)), oxides=oxides)

    @app.put("/session/{session_id}/profile", status_code=204)
    def put_profile(session_id: UUID, profile: SlagProfile, request: Request) -> Response:
        request.app.state.sessions.set_profile(session_id, profile)
        return Response(status_code=204)

    @app.post("/chat")
    def chat(chat_request: ChatRequest, request: Request) -> StreamingResponse:
        session = request.app.state.sessions.get_or_create(chat_request.session_id)
        # A plain generator: Starlette runs it in a thread, so blocking model calls are fine
        events = stream_answer(request.app.state.answer, chat_request.message, session)
        return StreamingResponse(events, media_type="text/event-stream", headers=SSE_HEADERS)

    return app


app = create_app()

"""The chat API: routes only, streaming answer_message as server-sent events (ADR 005).

Run with: uv run uvicorn backend.app:app --reload --port 8765
"""

import logging
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from typing import get_args
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.sessions import SessionStore
from steel_slag_slm.config import LlmGatewaySettings, load_llm_gateway_settings
from steel_slag_slm.data.slag_catalogue import OXIDE_LABELS
from steel_slag_slm.pipelines.answer_message import ChatSession, answer_message
from steel_slag_slm.pipelines.assistant_setup import ModelChoice, build_assistant
from steel_slag_slm.rag.similar_slags import SlagProfile
from steel_slag_slm.schemas import ChatEvent, SteelSlagType

logger = logging.getLogger(__name__)

AnswerFn = Callable[[str, ChatSession, ModelChoice], Iterator[ChatEvent]]

SERVER_ERROR_MESSAGE = "Something went wrong while answering. Please try again."
# no-transform/X-Accel-Buffering stop proxies from holding events back until the stream ends
SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}


class ChatRequest(BaseModel):
    """One message the user sends in a chat, and the models picked for it."""

    session_id: UUID
    message: str
    reader_model: str | None = None  # None: llm_gateway.reader_model
    answer_model: str | None = None  # None: llm_gateway.answer_model
    thinking: bool = False


class ModelOption(BaseModel):
    """One model the pickers offer."""

    id: str  # the gateway's name for it
    label: str
    supports_thinking: bool


class ModelOptions(BaseModel):
    """Every model the pickers offer, and which one each job uses by default."""

    models: list[ModelOption]
    reader_default: str
    answer_default: str


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


def choose_models(chat_request: ChatRequest, gateway: LlmGatewaySettings) -> ModelChoice:
    """The request's models, with the config defaults for any it leaves out.

    Raises:
        HTTPException: 422 if it names a model the gateway config doesn't list.
    """
    choice = ModelChoice(
        reader_model=chat_request.reader_model or gateway.reader_model,
        answer_model=chat_request.answer_model or gateway.answer_model,
        thinking=chat_request.thinking,
    )
    for model in (choice.reader_model, choice.answer_model):
        if model not in gateway.models:
            raise HTTPException(422, f"Unknown model {model!r}; GET /models lists them.")
    return choice


def stream_answer(
    answer: AnswerFn, message: str, session: ChatSession, choice: ModelChoice
) -> Iterator[str]:
    """Stream the answer's events, ending with one "error" event if answering fails midway."""
    try:
        for event in answer(message, session, choice):
            yield format_sse(event)
    except Exception:
        # The stream must still end with "done" or "error" (ADR 005), so turn any failure into one
        logger.exception("Answering failed")
        yield format_sse(ChatEvent(type="error", text=SERVER_ERROR_MESSAGE))


def create_app(
    answer: AnswerFn | None = None, gateway: LlmGatewaySettings | None = None
) -> FastAPI:
    """Build the API. Without an answer function (tests pass a fake), the real assistant is
    built at startup: the search index, models and BigQuery tools, once for every chat.
    Without gateway settings, they are read from base.yaml and .env."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.sessions = SessionStore()
        app.state.gateway = gateway or load_llm_gateway_settings()
        if answer:
            app.state.answer = answer
        else:
            # Slow (about a minute): loads the index from Drive and the embedding models
            assistant = build_assistant()
            app.state.answer = lambda message, session, choice: answer_message(
                message, session, assistant, choice
            )
        yield

    app = FastAPI(title="Steel slag chat", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/profile/options")
    def profile_options() -> ProfileOptions:
        oxides = [OxideOption(column=col, label=label) for col, label in OXIDE_LABELS.items()]
        return ProfileOptions(slag_types=list(get_args(SteelSlagType)), oxides=oxides)

    @app.get("/models")
    def models(request: Request) -> ModelOptions:
        gateway: LlmGatewaySettings = request.app.state.gateway
        options = [
            ModelOption(id=name, label=model.label, supports_thinking=model.supports_thinking)
            for name, model in gateway.models.items()
        ]
        return ModelOptions(
            models=options,
            reader_default=gateway.reader_model,
            answer_default=gateway.answer_model,
        )

    @app.put("/session/{session_id}/profile", status_code=204)
    def put_profile(session_id: UUID, profile: SlagProfile, request: Request) -> Response:
        request.app.state.sessions.set_profile(session_id, profile)
        return Response(status_code=204)

    @app.post("/chat")
    def chat(chat_request: ChatRequest, request: Request) -> StreamingResponse:
        choice = choose_models(chat_request, request.app.state.gateway)  # before streaming starts
        session = request.app.state.sessions.get_or_create(chat_request.session_id)
        # A plain generator: Starlette runs it in a thread, so blocking model calls are fine
        events = stream_answer(request.app.state.answer, chat_request.message, session, choice)
        return StreamingResponse(events, media_type="text/event-stream", headers=SSE_HEADERS)

    return app


app = create_app()

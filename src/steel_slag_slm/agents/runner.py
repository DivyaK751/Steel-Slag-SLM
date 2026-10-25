"""Build and run ADK agents that answer with one JSON object matching a pydantic schema."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner, Runner
from google.genai import types
from pydantic import BaseModel, ValidationError

from steel_slag_slm.config import AgentSettings
from steel_slag_slm.exceptions import AgentOutputError
from steel_slag_slm.schemas import TokenUsage

APP_NAME = "steel_slag_slm"
USER_ID = "pipeline"
# Vertex answers 429 when a burst exceeds the per-minute quota; back off and retry
LLM_RETRY_ATTEMPTS = 5

SchemaT = TypeVar("SchemaT", bound=BaseModel)
ItemT = TypeVar("ItemT")
ResultT = TypeVar("ResultT")


@dataclass(frozen=True, slots=True)
class StructuredAnswer(Generic[SchemaT]):
    """An agent's parsed answer and the tokens it took to produce it."""

    result: SchemaT
    usage: TokenUsage


def build_structured_agent(
    name: str, settings: AgentSettings, instruction: str, output_schema: type[BaseModel]
) -> LlmAgent:
    """Create a tool-less agent whose every answer is validated against output_schema."""
    return LlmAgent(
        name=name,
        model=settings.model,
        # A callable skips ADK's {state} templating, which would break the JSON braces in prompts
        instruction=lambda _context: instruction,
        output_schema=output_schema,
        generate_content_config=types.GenerateContentConfig(
            temperature=settings.temperature,
            http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(attempts=LLM_RETRY_ATTEMPTS)
            ),
        ),
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )


def build_runner(agent: LlmAgent) -> Runner:
    """Wrap an agent in a runner that keeps sessions in memory only."""
    return InMemoryRunner(agent=agent, app_name=APP_NAME)


async def run_structured(
    runner: Runner,
    message: str,
    schema: type[SchemaT],
    attachments: Sequence[types.Part] = (),
) -> SchemaT:
    """Send one message, plus any attachments (e.g. PDF pages), and parse the final answer.

    Raises:
        AgentOutputError: If the agent gives no final answer or it doesn't match the schema.
    """
    answer = await run_structured_with_usage(runner, message, schema, attachments)
    return answer.result


async def run_structured_with_usage(
    runner: Runner,
    message: str,
    schema: type[SchemaT],
    attachments: Sequence[types.Part] = (),
) -> StructuredAnswer[SchemaT]:
    """Like run_structured, but also return the tokens the agent's model calls used.

    Raises:
        AgentOutputError: If the agent gives no final answer or it doesn't match the schema.
    """
    # A new session per item, so one paper's context can never leak into the next
    session = await runner.session_service.create_session(app_name=APP_NAME, user_id=USER_ID)
    content = types.Content(role="user", parts=[types.Part(text=message), *attachments])
    final_text = ""
    usages: list[Any] = []
    try:
        async for event in runner.run_async(
            user_id=USER_ID, session_id=session.id, new_message=content
        ):
            usage = getattr(event, "usage_metadata", None)
            if usage is not None and not getattr(event, "partial", False):
                usages.append(usage)
            if event.is_final_response() and event.content and event.content.parts:
                final_text = "".join(part.text or "" for part in event.content.parts)
    finally:
        await runner.session_service.delete_session(
            app_name=APP_NAME, user_id=USER_ID, session_id=session.id
        )
    if not final_text.strip():
        raise AgentOutputError("Agent returned no final answer")
    try:
        result = schema.model_validate_json(final_text)
    except ValidationError as error:
        raise AgentOutputError(f"Answer doesn't match {schema.__name__}: {error}") from error
    return StructuredAnswer(result, _total_usage(usages))


def _total_usage(usages: Sequence[Any]) -> TokenUsage:
    """Add up the usage metadata of every model call (one per call, without tools)."""
    return TokenUsage(
        input_tokens=sum(u.prompt_token_count or 0 for u in usages),
        cached_tokens=sum(u.cached_content_token_count or 0 for u in usages),
        output_tokens=sum(
            (u.candidates_token_count or 0) + (u.thoughts_token_count or 0) for u in usages
        ),
    )


async def gather_bounded(
    items: Sequence[ItemT],
    func: Callable[[ItemT], Awaitable[ResultT]],
    max_concurrency: int,
) -> list[ResultT | Exception]:
    """Run func over items with at most max_concurrency in flight, keeping input order.

    A failing item returns its exception in place of a result, so one bad item can't sink
    the batch; the caller decides which exceptions are expected.
    """
    semaphore = asyncio.Semaphore(max_concurrency)

    async def run_one(item: ItemT) -> ResultT:
        async with semaphore:
            return await func(item)

    results = await asyncio.gather(*(run_one(item) for item in items), return_exceptions=True)
    # gather also returns BaseExceptions (e.g. KeyboardInterrupt); those must stop the run
    for result in results:
        if isinstance(result, BaseException) and not isinstance(result, Exception):
            raise result
    return list(results)  # type: ignore[arg-type]

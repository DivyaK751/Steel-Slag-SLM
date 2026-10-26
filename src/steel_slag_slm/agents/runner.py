"""Build and run ADK agents that answer with one JSON object matching a pydantic schema."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar

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
            thinking_config=(
                types.ThinkingConfig(
                    thinking_level=types.ThinkingLevel(settings.thinking_level.upper())
                )
                if settings.thinking_level
                else None
            ),
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
    answer, _ = await run_structured_with_usage(runner, message, schema, attachments)
    return answer


async def run_structured_with_usage(
    runner: Runner,
    message: str,
    schema: type[SchemaT],
    attachments: Sequence[types.Part] = (),
) -> tuple[SchemaT, TokenUsage]:
    """Like run_structured, but also return the tokens the call used, for cost tracking.

    Raises:
        AgentOutputError: If the agent gives no final answer or it doesn't match the schema.
    """
    # A new session per item, so one paper's context can never leak into the next
    session = await runner.session_service.create_session(app_name=APP_NAME, user_id=USER_ID)
    content = types.Content(role="user", parts=[types.Part(text=message), *attachments])
    final_text = ""
    usage = TokenUsage()
    try:
        async for event in runner.run_async(
            user_id=USER_ID, session_id=session.id, new_message=content
        ):
            usage += _event_usage(event)
            if event.is_final_response() and event.content and event.content.parts:
                final_text = "".join(part.text or "" for part in event.content.parts)
    finally:
        await runner.session_service.delete_session(
            app_name=APP_NAME, user_id=USER_ID, session_id=session.id
        )
    if not final_text.strip():
        raise AgentOutputError("Agent returned no final answer")
    try:
        return schema.model_validate_json(final_text), usage
    except ValidationError as error:
        raise AgentOutputError(f"Answer doesn't match {schema.__name__}: {error}") from error


def _event_usage(event: Any) -> TokenUsage:
    """Token counts on one ADK event; events that didn't call the model report none."""
    metadata = getattr(event, "usage_metadata", None)
    if metadata is None:
        return TokenUsage()
    return TokenUsage(
        input_tokens=metadata.prompt_token_count or 0,
        output_tokens=metadata.candidates_token_count or 0,
        thinking_tokens=metadata.thoughts_token_count or 0,
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

"""Describe one parsed table (what it measures, its symbols, units and samples) with Gemini."""

import re

from google.adk.agents import LlmAgent
from google.adk.runners import Runner

from steel_slag_slm.agents.runner import build_structured_agent, run_structured_with_usage
from steel_slag_slm.config import TableDescriptionSettings
from steel_slag_slm.exceptions import AgentOutputError
from steel_slag_slm.llm.prompts import PromptTemplate
from steel_slag_slm.schemas import TableDescription, TableToDescribe, TokenUsage

AGENT_NAME = "table_describer"
MISSING_FIELD = "(none)"
# A number standing on its own: "28" in "28 d", but not the "2" of "CO2" or "Al2O3"
_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?")
# An answer with invented numbers is asked again once, naming them; the answers are
# deterministic, so asking without saying what was wrong would repeat it
MAX_ATTEMPTS = 2


def build_table_describer_agent(
    settings: TableDescriptionSettings, prompt: PromptTemplate
) -> LlmAgent:
    """Create the table-describing agent from its settings and prompt."""
    return build_structured_agent(AGENT_NAME, settings, prompt.instruction, TableDescription)


def table_name(table: TableToDescribe) -> str:
    """How the table is named in messages and reports, e.g. "Table 3"."""
    return table.label or f"uncaptioned table (block {table.block_index})"


def ungrounded_numbers(description: TableDescription, source: str) -> list[str]:
    """Return the numbers of the description that the source text never prints."""
    printed = {_normalize(number) for number in _NUMBER.findall(source)}
    written = _NUMBER.findall(description.model_dump_json())
    return sorted({number for number in written if _normalize(number) not in printed})


def _printed_text(table: TableToDescribe) -> str:
    """Everything the describer was shown, so any number it repeats from there is allowed."""
    parts = [table.paper_title, table.section, table.table, table.previous_table, table.context]
    return "\n".join(parts)


def _normalize(number: str) -> str:
    return number.replace(",", ".")  # "10,6" and "10.6" are the same printed value


async def describe_table(
    runner: Runner, prompt: PromptTemplate, table: TableToDescribe
) -> tuple[TableDescription, TokenUsage]:
    """Ask the agent what the table reports, and how many tokens that took (all attempts).

    An answer stating a number that none of the inputs print (a sign of an invented value)
    is asked again once, naming those numbers.

    Raises:
        AgentOutputError: If an answer is missing or malformed, or every attempt states
            numbers that none of the inputs print.
    """
    usage = TokenUsage()
    correction = MISSING_FIELD
    for _ in range(MAX_ATTEMPTS):
        answer = await run_structured_with_usage(
            runner, _render(prompt, table, correction), TableDescription
        )
        usage += answer.usage
        invented = ungrounded_numbers(answer.result, _printed_text(table))
        if not invented:
            return answer.result, usage
        correction = ", ".join(invented)
    raise AgentOutputError(
        f"Description states numbers its inputs never print: {correction}",
        answer.result.model_dump_json(),
        usage=usage,
    )


def _render(prompt: PromptTemplate, table: TableToDescribe, correction: str) -> str:
    return prompt.render_input(
        paper_id=table.paper_id,
        paper_title=table.paper_title or MISSING_FIELD,
        section=table.section or MISSING_FIELD,
        table_label=table.label or MISSING_FIELD,
        table=table.table,
        previous_table=table.previous_table or MISSING_FIELD,
        context=table.context or MISSING_FIELD,
        correction=correction,
    )

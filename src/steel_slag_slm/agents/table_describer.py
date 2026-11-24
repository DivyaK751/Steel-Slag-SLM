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


def build_table_describer_agent(
    settings: TableDescriptionSettings, prompt: PromptTemplate
) -> LlmAgent:
    """Create the table-describing agent from its settings and prompt."""
    return build_structured_agent(AGENT_NAME, settings, prompt.instruction, TableDescription)


def table_name(table: TableToDescribe) -> str:
    """How the table is named in messages and reports, e.g. "Table 3"."""
    return table.label or f"uncaptioned table (block {table.block_index})"


def ungrounded_numbers(description: TableDescription, source: str) -> list[str]:
    """Return the numbers of the description that its table and context never print."""
    printed = {_normalize(number) for number in _NUMBER.findall(source)}
    written = _NUMBER.findall(description.model_dump_json())
    return sorted({number for number in written if _normalize(number) not in printed})


def _normalize(number: str) -> str:
    return number.replace(",", ".")  # "10,6" and "10.6" are the same printed value


async def describe_table(
    runner: Runner, prompt: PromptTemplate, table: TableToDescribe
) -> tuple[TableDescription, TokenUsage]:
    """Ask the agent what the table reports, and how many tokens that took.

    Raises:
        AgentOutputError: If the answer is missing or malformed, or states a number that the
            table and its context do not print (a sign of an invented value).
    """
    message = prompt.render_input(
        paper_id=table.paper_id,
        table_label=table.label or MISSING_FIELD,
        table=table.table,
        context=table.context or MISSING_FIELD,
    )
    answer = await run_structured_with_usage(runner, message, TableDescription)
    invented = ungrounded_numbers(answer.result, f"{table.table}\n{table.context}")
    if invented:
        raise AgentOutputError(
            f"Description states numbers the table does not print: {', '.join(invented)}",
            answer.result.model_dump_json(),
            usage=answer.usage,
        )
    return answer.result, answer.usage

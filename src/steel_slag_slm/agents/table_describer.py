"""Describe one parsed table (what it measures, its symbols, units and samples) with Gemini."""

import asyncio
import re
from dataclasses import dataclass
from typing import Literal

from google.adk.agents import LlmAgent
from google.adk.runners import Runner

from steel_slag_slm.agents.runner import build_structured_agent, run_structured_with_usage
from steel_slag_slm.config import TableDescriptionSettings
from steel_slag_slm.data.table_text import table_caption, table_rows
from steel_slag_slm.exceptions import AgentOutputError
from steel_slag_slm.llm.prompts import PromptTemplate
from steel_slag_slm.schemas import DescribedColumn, TableDescription, TableToDescribe, TokenUsage

AGENT_NAME = "table_describer"
MISSING_FIELD = "(none)"
# A number standing on its own in the description: "28" in "28 d", not the "2" of "CO2"
_WRITTEN_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?")
# Any digits the inputs print count as printed, even glued to text: "p28d", "ε4", "Table2."
_PRINTED_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
# Control characters copied from a PDF would make Gemini's JSON answer invalid
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# An answer that fails is asked again, naming invented numbers; the answers are
# deterministic, so asking without saying what was wrong would repeat it
MAX_ATTEMPTS = 2


@dataclass(frozen=True, slots=True)
class DescribedTable:
    """A table's description, where it came from, and the tokens all attempts used."""

    description: TableDescription
    usage: TokenUsage
    source: Literal["gemini", "paper"] = "gemini"  # "paper": no Gemini answer passed
    problem: str | None = None  # why no Gemini answer passed, for the run's log


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
    printed = {_normalize(number) for number in _PRINTED_NUMBER.findall(source)}
    printed |= set(re.findall(r"\d+", source))  # "28" of "28.79" or "p28d"
    written = _WRITTEN_NUMBER.findall(description.model_dump_json())
    return sorted({number for number in written if _normalize(number) not in printed})


def paper_description(table: TableToDescribe) -> TableDescription:
    """Describe a table in the paper's own words only: its caption (or "Table N") and headers."""
    rows = table_rows(table.table)
    headers = [cell for cell in rows[0] if cell] if rows else []
    caption = table_caption(table.table).replace("\n", " ")
    return TableDescription(
        summary=caption or table.label or "",
        columns=[DescribedColumn(label=header) for header in headers],
    )


def _printed_text(table: TableToDescribe) -> str:
    """Everything the describer was shown, so any number it repeats from there is allowed."""
    parts = [
        table.paper_title, table.section, table.label or "", table.table, table.previous_table,
        table.context,
    ]  # fmt: skip
    return "\n".join(parts)


def _normalize(number: str) -> str:
    return number.replace(",", ".")  # "10,6" and "10.6" are the same printed value


async def describe_table(
    runner: Runner, prompt: PromptTemplate, table: TableToDescribe, timeout_s: float
) -> DescribedTable:
    """Describe a table; every table gets a description, never an error for a bad answer.

    An answer that is malformed, takes over timeout_s, or states a number no input prints
    (a sign of an invented value) is asked again, naming those numbers. If no attempt
    passes, Gemini's answers are dropped and the description holds only the paper's own
    words, so no unchecked text reaches search. Errors reaching Gemini itself (login, quota,
    outage) are raised, so a rerun retries the table.
    """
    usage = TokenUsage()
    correction = MISSING_FIELD
    problem = ""
    for _ in range(MAX_ATTEMPTS):
        try:
            answer = await asyncio.wait_for(
                run_structured_with_usage(
                    runner, _render(prompt, table, correction), TableDescription
                ),
                timeout_s,
            )
        except AgentOutputError as error:
            usage += error.usage or TokenUsage()
            problem = str(error)
            continue
        except TimeoutError:
            problem = f"no answer within {timeout_s:g} s"
            continue
        usage += answer.usage
        invented = ungrounded_numbers(answer.result, _printed_text(table))
        if not invented:
            return DescribedTable(answer.result, usage)
        correction = ", ".join(invented)
        problem = f"states numbers its inputs never print: {correction}"
    return DescribedTable(paper_description(table), usage, "paper", problem)


def _render(prompt: PromptTemplate, table: TableToDescribe, correction: str) -> str:
    fields = {
        "paper_id": table.paper_id,
        "paper_title": table.paper_title or MISSING_FIELD,
        "section": table.section or MISSING_FIELD,
        "table_label": table.label or MISSING_FIELD,
        "table": table.table,
        "previous_table": table.previous_table or MISSING_FIELD,
        "context": table.context or MISSING_FIELD,
        "correction": correction,
    }
    return prompt.render_input(
        **{name: _CONTROL_CHARACTERS.sub("", value) for name, value in fields.items()}
    )

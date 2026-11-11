"""Gemini agents that grade extracted rows against the reference and check disputes."""

import json
from collections.abc import Mapping, Sequence

from google.adk.agents import LlmAgent
from google.adk.runners import Runner

from steel_slag_slm.agents.runner import (
    StructuredAnswer,
    build_structured_agent,
    run_structured_with_usage,
)
from steel_slag_slm.config import ExtractionJudgeSettings
from steel_slag_slm.data.extraction_tables import (
    SheetTable,
    column_unit,
    data_columns,
    is_filled,
)
from steel_slag_slm.llm.prompts import PromptTemplate
from steel_slag_slm.schemas import Adjudication, JudgedCell, TableJudgement

JUDGE_NAME = "extraction_judge"
ADJUDICATOR_NAME = "extraction_adjudicator"
# Labels shown to the judge as pairing hints; the paper ID is the same on every row
_HINT_HEADERS = ("Material_ID", "Experiment_ID", "Mix_ID")

Row = Mapping[str, str | None]


def build_judge_agent(settings: ExtractionJudgeSettings, prompt: PromptTemplate) -> LlmAgent:
    """Create the agent that pairs rows and judges cells of one paper's sheet."""
    return build_structured_agent(JUDGE_NAME, settings, prompt.instruction, TableJudgement)


def build_adjudicator_agent(settings: ExtractionJudgeSettings, prompt: PromptTemplate) -> LlmAgent:
    """Create the agent that checks disputed extracted values against the paper's text."""
    return build_structured_agent(ADJUDICATOR_NAME, settings, prompt.instruction, Adjudication)


def format_rows(prefix: str, rows: Sequence[Row], sheet_table: SheetTable) -> str:
    """One line per row, numbered from 1 (R1, R2 ...), with only its non-empty cells."""
    shown = (*_HINT_HEADERS, *data_columns(sheet_table))
    lines = []
    for number, row in enumerate(rows, start=1):
        cells = {header: row[header] for header in shown if is_filled(row.get(header))}
        lines.append(f"{prefix}{number}: {json.dumps(cells, ensure_ascii=False)}")
    return "\n".join(lines) or "(none)"


def format_columns(sheet_table: SheetTable) -> str:
    """The sheet's data columns, each with its unit where the column has one."""
    lines = []
    for header in data_columns(sheet_table):
        unit = column_unit(header)
        lines.append(f"- {header}" + (f" [{unit}]" if unit else ""))
    return "\n".join(lines)


async def judge_table(
    runner: Runner,
    prompt: PromptTemplate,
    sheet_table: SheetTable,
    reference_rows: Sequence[Row],
    extracted_rows: Sequence[Row],
) -> StructuredAnswer[TableJudgement]:
    """Ask the judge to pair one paper's reference and extracted rows of a sheet.

    Raises:
        AgentOutputError: If the judge's answer is missing or malformed.
    """
    message = prompt.render_input(
        sheet=sheet_table.sheet,
        columns=format_columns(sheet_table),
        reference_rows=format_rows("R", reference_rows, sheet_table),
        extracted_rows=format_rows("E", extracted_rows, sheet_table),
    )
    return await run_structured_with_usage(runner, message, TableJudgement)


def format_claims(claims: Sequence[tuple[JudgedCell, Row]], sheet_names: Mapping[str, str]) -> str:
    """One JSON line per disputed cell, numbered from 1, with the extracted row it sits in."""
    lines = []
    for claim_id, (cell, row) in enumerate(claims, start=1):
        context = {header: value for header, value in row.items() if is_filled(value)}
        context.pop("Source_ID", None)
        claim = {
            "claim_id": claim_id,
            "sheet": sheet_names[cell.table],
            "column": cell.column,
            "system_value": cell.extracted_value,
            "key_value": cell.reference_value,
            "row": context,
        }
        lines.append(json.dumps(claim, ensure_ascii=False))
    return "\n".join(lines)


async def adjudicate_paper(
    runner: Runner,
    prompt: PromptTemplate,
    openalex_id: str,
    markdown: str,
    claims: str,
) -> StructuredAnswer[Adjudication]:
    """Ask whether the paper supports each disputed extracted value (claims from format_claims).

    Raises:
        AgentOutputError: If the adjudicator's answer is missing or malformed.
    """
    message = prompt.render_input(openalex_id=openalex_id, claims=claims, markdown=markdown)
    return await run_structured_with_usage(runner, message, Adjudication)

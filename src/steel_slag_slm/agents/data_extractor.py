"""Extract every workbook data value a parsed paper reports with Gemini."""

import logging
import re
from collections import Counter
from collections.abc import Sequence

from google.adk.agents import LlmAgent
from google.adk.runners import Runner

from steel_slag_slm.agents.runner import (
    StructuredAnswer,
    build_structured_agent,
    run_structured_with_usage,
)
from steel_slag_slm.config import DataExtractionSettings
from steel_slag_slm.exceptions import AgentOutputError
from steel_slag_slm.llm.prompts import PromptTemplate
from steel_slag_slm.schemas import (
    DataSheet,
    ExtractedMaterial,
    FigureReading,
    PaperExtraction,
    PaperFigure,
    ParsedPaper,
    RowExtraction,
)

logger = logging.getLogger(__name__)

AGENT_NAME = "data_extractor"
MISSING_FIELD = "(not available)"
# Prompts from v9 declare this variable; older ones get the Markdown alone, as they were run
FIGURES_VARIABLE = "figures"
# Prompts with this output_format are answered in rows (RowExtraction), older ones in facts
ROWS_FORMAT = "rows"
NO_FIGURES = "(no figure readings for this paper)"
# A paper whose answer is cut off at the output limit is asked again in these parts, one
# call each; the first part also names the paper's materials. The heaviest sheets
# (concrete, durability, carbon uptake) are spread over both parts
EXTRACTION_PARTS: tuple[tuple[DataSheet, ...], ...] = (
    (
        "sheet_02_feedstock_chemistry", "sheet_03_processing", "sheet_04_particle_size",
        "sheet_06_concrete_performance",
    ),
    ("sheet_05_activation", "sheet_07_durability", "sheet_08_carbon_uptake"),
)  # fmt: skip
_FACT_SHEET = re.compile(r'"sheet":\s*"(sheet_\d\d)_\w*"')
_FACT_EXPERIMENT = re.compile(r'"experiment_id":\s*"([^"]*)"')
NEW_MATERIALS = (
    "Return materials as usual, numbering the paper's steel slags MAT-0001, MAT-0002, ..."
)


def build_data_extractor_agent(
    settings: DataExtractionSettings, prompt: PromptTemplate
) -> LlmAgent:
    """Create the data-extraction agent from its settings and prompt."""
    return build_structured_agent(AGENT_NAME, settings, prompt.instruction, _answer_schema(prompt))


def _answer_schema(prompt: PromptTemplate) -> type[PaperExtraction] | type[RowExtraction]:
    return RowExtraction if prompt.output_format == ROWS_FORMAT else PaperExtraction


def describe_cut_answer(answer: str) -> str:
    """Summarise a cut-off answer: records (facts or rows) per sheet and their experiments."""
    sheets = Counter(_FACT_SHEET.findall(answer))
    experiments = len(set(_FACT_EXPERIMENT.findall(answer)))
    per_sheet = ", ".join(f"{sheet} {count}" for sheet, count in sorted(sheets.items()))
    total = sum(sheets.values())
    return f"{total} records ({per_sheet or 'none'}), {experiments} experiment IDs"


def _materials_note(materials: Sequence[ExtractedMaterial]) -> str:
    """Tell a later part which material_ids the first part gave the paper's steel slags."""
    listed = "\n".join(
        f"    {m.material_id} ({m.steel_slag_type}): {m.description}" for m in materials
    )
    return (
        "The paper's steel slags were already identified as below. Return these materials"
        " with the same material_id values, and add one (with the next free number) only"
        f" for a steel slag not listed:\n{listed}"
    )


def merge_parts(
    answers: Sequence[StructuredAnswer[PaperExtraction]],
    parts: Sequence[Sequence[DataSheet]] = EXTRACTION_PARTS,
) -> StructuredAnswer[PaperExtraction]:
    """Join the answers for each part into one extraction, with their token usage summed.

    Literature comes from the first answer, materials from the first plus any new ones a
    later answer adds, and each answer contributes facts for its own part's sheets only.
    """
    first = answers[0].result
    materials = list(first.materials)
    known = {material.material_id for material in materials}
    facts = []
    for answer, sheets in zip(answers, parts, strict=True):
        materials += [m for m in answer.result.materials if m.material_id not in known]
        known |= {material.material_id for material in answer.result.materials}
        facts += [fact for fact in answer.result.facts if fact.sheet in sheets]
    usage = answers[0].usage
    for answer in answers[1:]:
        usage = usage + answer.usage
    merged = PaperExtraction(literature=first.literature, materials=materials, facts=facts)
    return StructuredAnswer(merged, usage)


async def _ask(
    runner: Runner, prompt: PromptTemplate, text: str
) -> StructuredAnswer[PaperExtraction]:
    """One call, its answer read in the prompt's format and returned as facts."""
    if prompt.output_format != ROWS_FORMAT:
        return await run_structured_with_usage(runner, text, PaperExtraction)
    answer = await run_structured_with_usage(runner, text, RowExtraction)
    return StructuredAnswer(answer.result.to_extraction(), answer.usage)


async def extract_paper_data(
    runner: Runner,
    prompt: PromptTemplate,
    part_prompt: PromptTemplate,
    paper: ParsedPaper,
    title: str | None,
) -> StructuredAnswer[PaperExtraction]:
    """Ask the agent for every value the paper's parsed Markdown reports, and its token usage.

    The paper is asked about once. If that answer is cut off at the output limit, it is asked
    again once per part of EXTRACTION_PARTS, later parts reusing the material_ids the first
    gave; the usage then includes the cut-off call, which is billed too.

    Raises:
        AgentOutputError: If the agent's answer (to any part) is missing or malformed.
    """
    values = {
        "openalex_id": paper.openalex_id,
        "title": title or MISSING_FIELD,
        "markdown": paper.markdown,
    }
    if FIGURES_VARIABLE in prompt.variables:
        values[FIGURES_VARIABLE] = format_figures(paper.figures)
    paper_input = prompt.render_input(**values)
    try:
        return await _ask(runner, prompt, paper_input)
    except AgentOutputError as error:
        if not error.cut_off:
            raise AgentOutputError(
                f"{error}; the answer held {describe_cut_answer(error.answer)}",
                error.answer,
                usage=error.usage,
            ) from error
        logger.warning(
            "%s: answer cut off at the output limit (%s); asking again in %d parts",
            paper.openalex_id, describe_cut_answer(error.answer), len(EXTRACTION_PARTS),
        )  # fmt: skip
        in_parts = await _ask_in_parts(runner, prompt, part_prompt, paper_input)
        usage = in_parts.usage + error.usage if error.usage else in_parts.usage
        return StructuredAnswer(in_parts.result, usage)


async def _ask_in_parts(
    runner: Runner, prompt: PromptTemplate, part_prompt: PromptTemplate, paper_input: str
) -> StructuredAnswer[PaperExtraction]:
    """Ask about the paper once per part of EXTRACTION_PARTS and merge the answers."""
    answers: list[StructuredAnswer[PaperExtraction]] = []
    for number, sheets in enumerate(EXTRACTION_PARTS, start=1):
        materials = _materials_note(answers[0].result.materials) if answers else NEW_MATERIALS
        part = part_prompt.render_input(sheets=", ".join(sheets), materials=materials)
        try:
            answers.append(await _ask(runner, prompt, paper_input + part))
        except AgentOutputError as error:
            raise AgentOutputError(
                f"Part {number} of {len(EXTRACTION_PARTS)}: {error};"
                f" the answer held {describe_cut_answer(error.answer)}",
                error.answer,
            ) from error
    return merge_parts(answers)


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "/")


def _caption(text: str) -> str:
    """The caption once: the parser sometimes stores it twice in a row."""
    caption = " ".join(text.split())
    half = len(caption) // 2
    for cut in range(max(half - 2, 1), half + 3):
        if caption[:cut].strip() == caption[cut:].strip():
            return caption[:cut].strip()
    return caption


def _reading_lines(reading: FigureReading) -> list[str]:
    """A reading's values as Markdown: a table of the plotted values, phases, process steps."""
    lines = [f"Type: {reading.figure_type}. {reading.summary}"]
    if reading.columns and reading.rows:
        headers = [
            f"{column.label} ({column.unit})" if column.unit else column.label
            for column in reading.columns
        ]
        lines += [
            "| " + " | ".join(map(_cell, headers)) + " |",
            "|" + " --- |" * len(headers),
            *("| " + " | ".join(map(_cell, row)) + " |" for row in reading.rows),
        ]
    if reading.phases:
        lines.append("Phases labelled: " + "; ".join(reading.phases))
    for step in reading.steps:
        conditions = f" ({'; '.join(step.conditions)})" if step.conditions else ""
        lines.append(f"Step {step.id}: {step.label}{conditions}")
    for flow in reading.flows:
        label = f" [{flow.label}]" if flow.label else ""
        lines.append(f"Flow: {flow.source} -> {flow.target}{label}")
    return lines


def format_figures(figures: Sequence[PaperFigure]) -> str:
    """Render figure readings as Markdown, one section per read figure in paper order."""
    sections = []
    for figure in figures:
        if figure.reading is None:
            continue
        number = figure.figure_number or "(unnumbered)"
        page = f", page {figure.pages[0]}" if figure.pages else ""
        lines = [f"### Figure {number}{page}", f"Caption: {_cell(_caption(figure.caption))}"]
        sections.append("\n".join(lines + _reading_lines(figure.reading)))
    return "\n\n".join(sections) or NO_FIGURES
